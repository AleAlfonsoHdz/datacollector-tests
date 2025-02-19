# Copyright 2022 StreamSets Inc.
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

import pytest
import logging
import json
import string
from pprint import pformat

from streamsets.testframework.markers import sdc_min_version
from streamsets.testframework.utils import get_random_string

logger = logging.getLogger(__name__)

LINEAGE_DIR = "/tmp/lineage_"+get_random_string(string.ascii_lowercase, 10)
ENABLE_LINEAGE_JSON_PUBLISHER = {
    'lineage.publishers': 'json',
    'lineage.publisher.json.def':
        ('streamsets-datacollector-basic-lib::'
         'com_streamsets_pipeline_lib_lineage_JSONLineagePublisher'),
    'lineage.publisher.json.config.outputDir': LINEAGE_DIR
}

@pytest.fixture(scope='module')
def sdc_common_hook(args):
    def hook(data_collector):
        data_collector.sdc_properties.update(ENABLE_LINEAGE_JSON_PUBLISHER)
    return hook

@sdc_min_version('5.4.0')
@pytest.mark.parametrize('records_to_be_generated', [100])
def test_lineage_json(sdc_builder, sdc_executor, records_to_be_generated):
    """
    Check if the JSON file generated by the lineage publisher is valid and contains the expected keys.

    Dev Data Generator >> trash
    """
    builder = sdc_builder.get_pipeline_builder()

    origin = builder.add_stage('Dev Data Generator')
    origin.set_attributes(records_to_be_generated=records_to_be_generated)
    origin.fields_to_generate = [{'field': 'foo', 'type': 'STRING'}]
    destination = builder.add_stage('Trash')
    origin >> destination
    pipeline = builder.build()

    sdc_executor.add_pipeline(pipeline)
    sdc_executor.start_pipeline(pipeline).wait_for_finished()
    lineage_json = sdc_executor.execute_shell(f'cat {LINEAGE_DIR}/pipeline*.json {LINEAGE_DIR}/test*.json').stdout
    sdc_executor.execute_shell(f'rm -rf {LINEAGE_DIR}/*.json')
    try:
        lineage_dict = json.loads(lineage_json)
        logger.info(f'Loaded JSON file: {pformat(lineage_dict)}')
    except Exception as e:
        assert False, f'Malformed lineage JSON file: {lineage_json}'

    for key in ["pipelineId", "permalink", "pipelineStartTime", "pipelineTitle", "pipelineUser", "properties", "tags"]:
        assert key in lineage_dict, f'key "{key}" not present in JSON file'
    for asset in lineage_dict["specificAttributes"]["assets"]:
        for key in ["eventType", "stageName"]:
            assert key in asset, f'key "{key}" not present in JSON file'
