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

import logging
import string
import json

import pytest
from streamsets.testframework.markers import elasticsearch, sdc_min_version
from streamsets.testframework.utils import get_random_string

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@elasticsearch
def test_elasticsearch_origin(sdc_builder, sdc_executor, elasticsearch):
    """Test for Elasticsearch origin stage. We do so by putting data via Elastisearch client and reading via
    Elastisearch origin pipeline. To assert, we will snapshot the pipeline.
    The pipeline looks like:

    Elasticsearch origin pipeline:
        es_origin >> trash
    """
    es_index = get_random_string(string.ascii_letters, 10).lower()  # Elasticsearch indexes must be lower case
    es_doc_id = get_random_string(string.ascii_letters, 10)
    raw_str = 'Hello World!'
    raw = {'body': raw_str}

    builder = sdc_builder.get_pipeline_builder()
    es_origin = builder.add_stage('Elasticsearch', type='origin')
    es_origin.set_attributes(index=es_index, query="{'query': {'match_all': {}}}")
    trash = builder.add_stage('Trash')

    es_origin >> trash
    es_origin_pipeline = builder.build().configure_for_environment(elasticsearch)
    sdc_executor.add_pipeline(es_origin_pipeline)

    try:
        # Put data to Elasticsearch
        elasticsearch.client.create_document(es_index, es_doc_id, raw)

        # Run pipeline and assert
        snapshot = sdc_executor.capture_snapshot(es_origin_pipeline, start_pipeline=True).snapshot
        # no need to stop pipeline - as ES origin shuts off once data is read from Elasticsearch
        snapshot_data = snapshot[es_origin.instance_name].output[0].field
        # assert ES meta
        assert snapshot_data['_index'].value == es_index and snapshot_data['_id'].value == es_doc_id
        # assert ES data
        assert snapshot_data['_source']['body'].value == raw_str
    finally:
        # Clean up test data in ES
        elasticsearch.client.delete_index(es_index)


@elasticsearch
@sdc_min_version('3.0.0.0') # stop_after_first_batch
def test_elasticsearch_pipeline_errors(sdc_builder, sdc_executor, elasticsearch):
    """Test for a pipeline's error records being pumped to Elasticsearch. We do so by making a Dev Raw Data source
    target to Error stage which would send records to the pipeline configured Elasticsearch error records handling.
    We then assert the error records what we find in Elasticsearch. The pipeline would look like:

    Elasticsearch error pipeline:
        dev_raw_data_source >> error_target
    """
    # Test static
    es_index = get_random_string(string.ascii_letters, 10).lower()  # Elasticsearch indexes must be lower case
    es_mapping = get_random_string(string.ascii_letters, 10)
    es_doc_id = get_random_string(string.ascii_letters, 10)
    raw_str = 'Hello World!'

    # Build pipeline
    builder = sdc_builder.get_pipeline_builder()
    errstg = builder.add_error_stage('Write to Elasticsearch')
    errstg.set_attributes(document_id=es_doc_id, index=es_index, mapping=es_mapping)
    dev_raw_data_source = builder.add_stage('Dev Raw Data Source').set_attributes(data_format='TEXT',
                                                                                  stop_after_first_batch=True,
                                                                                  raw_data=raw_str)
    error_target = builder.add_stage('To Error')

    dev_raw_data_source >> error_target
    es_error_pipeline = builder.build().configure_for_environment(elasticsearch)
    sdc_executor.add_pipeline(es_error_pipeline)

    try:
        elasticsearch.client.create_index(es_index)

        # Run pipeline and read from Elasticsearch to assert
        sdc_executor.start_pipeline(es_error_pipeline).wait_for_finished()

        responses = elasticsearch.client.search(es_index)
        assert len(responses) == 1

        response = responses[0]
        assert response['_index'] == es_index
        assert response['_id'] == es_doc_id
        assert response['_type'] == es_mapping
        assert response['_source'] == {'text': raw_str}
    finally:
        # Clean up test data in ES
        elasticsearch.client.delete_index(es_index)


@sdc_min_version('3.7.0') # SDC-10408 Additional Properties
@elasticsearch
@pytest.mark.parametrize('additional_properties', ['{}', '{"_retry_on_conflict":3}'])
def test_elasticsearch_target(sdc_builder, sdc_executor, elasticsearch, additional_properties):
    """Test for Elasticsearch target stage. We do so by ingesting data via Dev Raw Data source to
    Elasticsearch stage and then asserting what we ingest to what will be read from Elasticsearch.
    The pipeline looks like:

    Elasticsearch target pipeline:
        dev_raw_data_source >> es_target
    """
    # Test static
    es_index = get_random_string(string.ascii_letters, 10).lower()  # Elasticsearch indexes must be lower case
    es_mapping = get_random_string(string.ascii_letters, 10)
    es_doc_id = get_random_string(string.ascii_letters, 10)
    raw_str = 'Hello World!'

    # Build pipeline
    builder = sdc_builder.get_pipeline_builder()
    dev_raw_data_source = builder.add_stage('Dev Raw Data Source').set_attributes(data_format='TEXT',
                                                                                  stop_after_first_batch=True,
                                                                                  raw_data=raw_str)
    es_target = builder.add_stage('Elasticsearch', type='destination')
    es_target.set_attributes(default_operation='INDEX', document_id=es_doc_id, index=es_index, mapping=es_mapping,
                             additional_properties=additional_properties)

    dev_raw_data_source >> es_target
    es_target_pipeline = builder.build().configure_for_environment(elasticsearch)
    es_target_pipeline.configuration["shouldRetry"] = False

    sdc_executor.add_pipeline(es_target_pipeline)

    try:
        elasticsearch.client.create_index(es_index)

        # Run pipeline and read from Elasticsearch to assert
        sdc_executor.start_pipeline(es_target_pipeline).wait_for_finished()

        # Since we are upsert on the same index, map, doc - there should only be one document (index 0)
        responses = elasticsearch.client.search(es_index)
        assert len(responses) == 1

        response = responses[0]
        assert response['_index'] == es_index
        assert response['_id'] == es_doc_id
        assert response['_type'] == es_mapping
        assert response['_source'] == {'text': raw_str}
    finally:
        # Clean up test data in ES
        elasticsearch.client.delete_index(es_index)


@sdc_min_version('3.17.0')
@elasticsearch
def test_elasticsearch_target_additional_properties(sdc_builder, sdc_executor, elasticsearch):
    """
    Elasticsearch target pipeline, adding additional properties, where specifies every routing with the value of the
    shard's record. It checks if the value of the record-label is added correctly to the property routing at
    ElasticSearch query.
        dev_raw_data_source >> es_target
    """
    # Test static
    index_values = []
    for _ in range(4):
        index_values.append(get_random_string(string.ascii_letters, 10).lower())

    # First  three records have 'shard' filled with data, last record does not
    raw_data = [{"text": "Record1", "index": index_values[0], "mapping": get_random_string(string.ascii_letters, 10).lower(),
                 "doc_id": get_random_string(string.ascii_letters, 10).lower(), "shard": "record1"},
                {"text": "Record2", "index": index_values[1], "mapping": get_random_string(string.ascii_letters, 10).lower(),
                 "doc_id": get_random_string(string.ascii_letters, 10).lower(), "shard": "record2"},
                {"text": "Record3", "index": index_values[2], "mapping": get_random_string(string.ascii_letters, 10).lower(),
                 "doc_id": get_random_string(string.ascii_letters, 10).lower(), "shard": "record3"},
                {"text": "Record4", "index": index_values[3], "mapping": get_random_string(string.ascii_letters, 10).lower(),
                 "doc_id": get_random_string(string.ascii_letters, 10).lower(), "shard": None}]

    # Build pipeline
    builder = sdc_builder.get_pipeline_builder()
    dev_raw_data_source = builder.add_stage('Dev Raw Data Source').set_attributes(data_format='JSON',
                                                                                  stop_after_first_batch=True,
                                                                                  raw_data='\n'.join(json.dumps(rec)
                                                                                                     for rec in raw_data))
    es_target = builder.add_stage('Elasticsearch', type='destination')
    es_target.set_attributes(default_operation='INDEX', document_id='${record:value(\'/doc_id\')}',
                             index='${record:value(\'/index\')}', mapping='${record:value(\'/mapping\')}',
                             additional_properties='{\"_routing\":${record:value(\'/shard\')}}')

    dev_raw_data_source >> es_target
    es_target_pipeline = builder.build().configure_for_environment(elasticsearch)

    sdc_executor.add_pipeline(es_target_pipeline)

    try:
        for es_index in index_values:
            elasticsearch.client.create_index(es_index)

        # Run pipeline with additional properties
        sdc_executor.start_pipeline(es_target_pipeline).wait_for_finished()

        responses = elasticsearch.client.search(','.join(index_values))
        assert len(responses) == 4

        def _sort_response(entry):
            return entry['_source']['text']

        responses.sort(key=_sort_response)

        for i in range(4):
            assert responses[i]['_index'] == raw_data[i]['index']
            assert responses[i]['_source']['text'] == raw_data[i]['text']

            # First three records have the value "shard" filled whereas the last record (id=3) does not and thus that
            # piece of metadata should never made it ElasticSearch.
            if i == 3:
                assert '_routing' not in responses[i]
            else:
                assert responses[0]['_routing'] == raw_data[0]['shard']
    finally:
        # Clean up test data in ES
        for es_index in index_values:
            elasticsearch.client.delete_index(es_index)


# SDC-11233: Elasticsearch origin does not properly upgrade single-threaded offsets
@elasticsearch
def test_offset_upgrade(sdc_builder, sdc_executor, elasticsearch):
    """Ensure that when upgrading from older offset format (that can be generated by either SCH or by upgrading
       pre-multithreaded pipeline) we properly upgrade the offset and the pipeline will not re-read everything
       from the source.
    """
    es_index = get_random_string(string.ascii_letters, 10).lower()
    es_doc_id = get_random_string(string.ascii_letters, 10)
    raw_str = 'Hello World!'
    raw = {'body': raw_str}

    builder = sdc_builder.get_pipeline_builder()
    es_origin = builder.add_stage('Elasticsearch', type='origin')
    es_origin.set_attributes(index=es_index, query="{'query': {'match_all': {}}}")
    trash = builder.add_stage('Trash')

    es_origin >> trash
    pipeline = builder.build().configure_for_environment(elasticsearch)
    sdc_executor.add_pipeline(pipeline)

    # We hard code offset to be pre-migration to multi-threaded origin and thus forcing the origin to upgrade it
    offset = {
        'offsets': {
            '$com.streamsets.datacollector.pollsource.offset$': None,
        },
        'version': 2
    }
    sdc_executor.api_client.update_pipeline_committed_offsets(pipeline.id, body=offset)

    try:
        # Put data to Elasticsearch
        elasticsearch.client.create_document(es_index, es_doc_id, raw)

        # Run pipeline and assert
        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
        # no need to stop pipeline - as ES origin shuts off once data is read from Elasticsearch
        snapshot_data = snapshot[es_origin.instance_name].output[0].field
        # assert ES meta
        assert snapshot_data['_index'] == es_index and snapshot_data['_id'] == es_doc_id
        # assert ES data
        assert snapshot_data['_source']['body'] == raw_str

        # Now let's validate that the offset doesn't have the poll key any more
        offset = sdc_executor.api_client.get_pipeline_committed_offsets(pipeline.id).response.json()
        assert offset is not None
        assert '$com.streamsets.datacollector.pollsource.offset$' not in offset['offsets']
    finally:
        # Clean up test data in ES
        elasticsearch.client.delete_index(es_index)


@sdc_min_version('3.17.0')
@elasticsearch
@pytest.mark.parametrize('join_credentials', [True, False])
def test_elasticsearch_credentials_format(sdc_builder, sdc_executor, elasticsearch, join_credentials):
    """
    Elasticsearch target pipeline where specifies two different formats for the credential values.
    First, it checks if the previous format "username:password" is also valid and then update the pipeline with the new
    format, user name and password into two different fields, and checks again.
        dev_raw_data_source >> es_target
    """
    # Test static
    es_index = get_random_string(string.ascii_letters, 10).lower()  # Elasticsearch indexes must be lower case
    es_mapping = get_random_string(string.ascii_letters, 10)
    es_doc_id = get_random_string(string.ascii_letters, 10)
    raw_str = 'Hello World!'

    if join_credentials:
        username = elasticsearch.username + ':' + elasticsearch.password
        password = ''
    else:
        username = elasticsearch.username
        password = elasticsearch.password

    # Build pipeline
    builder = sdc_builder.get_pipeline_builder()
    dev_raw_data_source = builder.add_stage('Dev Raw Data Source').set_attributes(data_format='TEXT',
                                                                                  stop_after_first_batch=True,
                                                                                  raw_data=raw_str)
    es_target = builder.add_stage('Elasticsearch', type='destination')
    es_target.set_attributes(default_operation='INDEX', document_id=es_doc_id, index=es_index, mapping=es_mapping,
                             use_security=True, user_name=username, password=password)

    dev_raw_data_source >> es_target
    es_target_pipeline = builder.build().configure_for_environment(elasticsearch)
    es_target_pipeline.configuration["shouldRetry"] = False

    sdc_executor.add_pipeline(es_target_pipeline)

    try:
        elasticsearch.client.create_index(es_index)

        # Run pipeline and read credential values from Elasticsearch to assert
        sdc_executor.start_pipeline(es_target_pipeline).wait_for_finished()

        # Since we are upsert on the same index, map, doc - there should only be one document (index 0)
        response = elasticsearch.client.search(es_index)
        assert len(response) == 1
        assert response[0]['_index'] == es_index
        assert response[0]['_id'] == es_doc_id
        assert response[0]['_type'] == es_mapping
        assert response[0]['_source'] == {'text': raw_str}
    finally:
        # Clean up test data in ES
        elasticsearch.client.delete_index(es_index)
