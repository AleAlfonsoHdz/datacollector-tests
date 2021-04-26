# Copyright 2017 StreamSets Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import logging

import pytest
from streamsets.testframework.markers import sdc_min_version

logger = logging.getLogger(__name__)

# pylint: disable=pointless-statement, redefined-outer-name, too-many-locals


@pytest.fixture(scope='module')
def sdc_common_hook():
    def hook(data_collector):
        data_collector.add_stage_lib('streamsets-datacollector-groovy_2_4-lib')

    return hook


def test_groovy_evaluator(sdc_builder, sdc_executor):
    """Test Groovy Evaluator processor. We test by setting up a template object in initialization script, then
    main processing script which access the template to create a Groovy object per record and to create a new record
    attribute. Finally in destroy script, we collate all the records for its new attribute and Groovy assert its value.
    The pipeline would look like:

        dev_raw_data_source >> groovy_evaluator >> wiretap
    """
    raw_company_1 = dict(name='StreamSets', floors=3)
    raw_company_2 = dict(name='Example Inc.', floors=1)
    raw_company_3 = dict(name='ASDF', floors=10)
    raw_company_4 = dict(name='QWER Inc.', floors=2)
    raw_data = json.dumps([raw_company_1, raw_company_2, raw_company_3, raw_company_4])

    pipeline_builder = sdc_builder.get_pipeline_builder()
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='JSON', json_content='ARRAY_OBJECTS', raw_data=raw_data,
                                       stop_after_first_batch=True)
    groovy_evaluator = pipeline_builder.add_stage('Groovy Evaluator', type='processor')
    # in the init script we create a 'Building' template object which can be cloned per each pipeline record processing
    init_script = """
        import groovy.transform.*

        @Canonical
        class Building implements Cloneable{
            String name
            int floors
            boolean officeSpace
        }

        state['building_template'] = new Building()
    """
    # in the script for every record processing, we compute if a building has more than 2 floors and
    # if so we create a record attribute 'officeSpace' to 'true' else 'false'
    script = """
        state['buildings'] = []
        for (record in records) {
          try {
            record.value['officeSpace'] = (record.value['floors'] > 2) ?: false

            def building = state['building_template'].clone()
            building.name = record.value['name']
            building.floors = record.value['floors']
            building.officeSpace = record.value['officeSpace']
            state['buildings'] << building
            output.write(record)
          } catch (e) {
            log.error(e.toString(), e)
            error.write(record, e.toString())
          }
        }
    """
    # in the destroy script, we collate all the building names which have 'officeSpace' and do a Groovy assert
    destroy_script = """
        def buildings = state['buildings']
        def mapBuildingName = { building -> building.name }
        def officeBuildingNames =
            buildings
                .stream()
                .filter { building ->
                    building.officeSpace && building.floors > 2
                }
                .map(mapBuildingName)
                .collect()

        assert officeBuildingNames == ['StreamSets', 'ASDF']
    """
    groovy_evaluator.set_attributes(destroy_script=destroy_script, enable_invokedynamic_compiler_option=True,
                                    init_script=init_script, record_processing_mode='BATCH', script=script)
    wiretap = pipeline_builder.add_wiretap()

    dev_raw_data_source >> groovy_evaluator >> wiretap.destination
    pipeline = pipeline_builder.build()
    sdc_executor.add_pipeline(pipeline)
    sdc_executor.start_pipeline(pipeline).wait_for_finished()

    # search for a record whose 'name' is raw_company_1['name'] and assert new attribute ('officeSpace') is created
    # with expected boolean value (where 'floors' > 2)
    record_1 = next(record for record in wiretap.output_records if record.field['name'] == raw_company_1['name'])
    assert record_1.field['officeSpace'] == (raw_company_1['floors'] > 2)
    record_2 = next(record for record in wiretap.output_records if record.field['name'] == raw_company_2['name'])
    assert record_2.field['officeSpace'] == (raw_company_2['floors'] > 2)


# SDC-10353: Unable to delete record headers in Scripting Evaluators
def test_delete_header_attribute(sdc_builder, sdc_executor):
    """Make sure that deleted attributes stays deleted."""
    builder = sdc_builder.get_pipeline_builder()

    origin = builder.add_stage('Dev Raw Data Source')
    origin.set_attributes(data_format='TEXT', raw_data="BLA BLA", stop_after_first_batch=True)

    expression = builder.add_stage('Expression Evaluator')
    expression.header_attribute_expressions = [
        {'attributeToSet': 'remove', 'headerAttributeExpression': 'I should be deleted'}
    ]

    groovy = builder.add_stage('Groovy Evaluator')
    groovy.init_script = ''
    groovy.destroy_script = ''
    groovy.script =  """
        for (record in records) {
            record.attributes.remove('remove')
            output.write(record)
        }
    """

    wiretap = builder.add_wiretap()

    origin >> expression >> groovy >> wiretap.destination

    pipeline = builder.build()
    sdc_executor.add_pipeline(pipeline)
    sdc_executor.start_pipeline(pipeline).wait_for_finished()

    assert len(wiretap.output_records) == 1
    assert 'remove' not in wiretap.output_records[0].header


# SDC-11546: Expose the underlying Data Collector Record in Scripting processors
@sdc_min_version('3.9.0')
def test_expose_sdc_record(sdc_builder, sdc_executor):
    """Ensure that underlying SDC record is accessible."""
    builder = sdc_builder.get_pipeline_builder()

    origin = builder.add_stage('Dev Raw Data Source')
    origin.set_attributes(data_format='TEXT', raw_data="BLA BLA", stop_after_first_batch=True)

    expression = builder.add_stage('Expression Evaluator')
    expression.field_attribute_expressions = [
        {"fieldToSet": "/text", "attributeToSet": "attr", "fieldAttributeExpression": "is-here"}
    ]

    groovy = builder.add_stage('Groovy Evaluator')
    groovy.init_script = ''
    groovy.destroy_script = ''
    groovy.script =  """
        for (record in records) {
            record.attributes['attr'] = record.sdcRecord.get('/text').getAttribute('attr')
            output.write(record)
        }
    """

    wiretap = builder.add_wiretap()

    origin >> expression >> groovy >> wiretap.destination

    pipeline = builder.build()
    sdc_executor.add_pipeline(pipeline)
    sdc_executor.start_pipeline(pipeline).wait_for_finished()

    assert len(wiretap.output_records) == 1
    # STF-798: RecordHeader is inconsistent in STF and SDC
    assert 'is-here' == wiretap.output_records[0].header['values']['attr']


# SDC-11555: Provide ability to use direct SDC record in scripting processors
@sdc_min_version('3.9.0')
def test_sdc_record(sdc_builder, sdc_executor):
    """Iterate over SDC record directly rather then JSR-223 wrapper."""
    builder = sdc_builder.get_pipeline_builder()

    origin = builder.add_stage('Dev Raw Data Source')
    origin.set_attributes(data_format='JSON', raw_data='{"old": "old-value"}')
    origin.stop_after_first_batch = True

    groovy = builder.add_stage('Groovy Evaluator')
    groovy.record_type = 'SDC_RECORDS'
    groovy.init_script = ''
    groovy.destroy_script = ''
    groovy.script =  """
        import com.streamsets.pipeline.api.Field
        for (record in records) {
            record.sdcRecord.set('/new', Field.create(Field.Type.STRING, 'new-value'))
            record.sdcRecord.get('/old').setAttribute('attr', 'attr-value')
            output.write(record)
        }
    """

    wiretap = builder.add_wiretap()

    origin >> groovy >> wiretap.destination

    pipeline = builder.build()
    sdc_executor.add_pipeline(pipeline)
    sdc_executor.start_pipeline(pipeline).wait_for_finished()

    assert len(wiretap.output_records) == 1
    assert 'new-value' == wiretap.output_records[0].field['new']
    assert 'attr-value' == wiretap.output_records[0].get_field_data('/old').attributes['attr']


def test_event_creation(sdc_builder, sdc_executor):
    """Ensure that the process is able to create events."""
    builder = sdc_builder.get_pipeline_builder()

    origin = builder.add_stage('Dev Raw Data Source')
    origin.set_attributes(data_format='JSON', raw_data='{"old": "old-value"}')
    origin.stop_after_first_batch = True

    groovy = builder.add_stage('Groovy Evaluator')
    groovy.init_script = ''
    groovy.destroy_script = ''
    groovy.script =  """
event = sdcFunctions.createEvent("event", 1)
event.value = sdcFunctions.createMap(true)
event.value['value'] = "secret"
sdcFunctions.toEvent(event)
"""

    records_wiretap = builder.add_wiretap()
    events_wiretap = builder.add_wiretap()

    origin >> groovy >> records_wiretap.destination
    groovy >= events_wiretap.destination

    pipeline = builder.build()
    sdc_executor.add_pipeline(pipeline)
    sdc_executor.start_pipeline(pipeline).wait_for_finished()

    assert len(events_wiretap.output_records) == 1
    assert events_wiretap.output_records[0].get_field_data('/value') == 'secret'
