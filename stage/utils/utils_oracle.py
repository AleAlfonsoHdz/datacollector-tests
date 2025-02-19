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
r"""Tools used for the revamped Oracle CDC Origin"""

from abc import ABC, abstractmethod
from contextlib import ExitStack
import logging
import pytest

from streamsets.testframework.markers import database


logger = logging.getLogger(__name__)

# These variables need to be loaded only once and will not change from test to test
SERVICE_NAME = ""  # The value will be assigned during setup
SYSTEM_IDENTIFIER = ""  # The value will be assigned during setup
RECORD_FORMATS = ["BASIC", "RICH"]


def _get_single_context_parameter(database, parameter):
    """Retrieve the value of a context parameter from the database,
    e.g. SERVICE_NAME or INSTANCE_NAME. The parameter must have one single value."""
    with ExitStack() as exit_stack:
        logger.info("Connect to DB")
        connection = database.engine.connect()
        exit_stack.callback(connection.close)

        query = f"SELECT SYS_CONTEXT('USERENV', '{parameter}') FROM DUAL"
        logger.info(f"Retrieve '{parameter}' with query: {query}")
        result = connection.execute(query)
        exit_stack.callback(result.close)

        result_values = result.fetchall()
        logger.info(f"Retrieved: {result_values}")

        assert len(result_values) == 1, f"Expected 1 {parameter} result, got '{result_values}'"
        assert len(result_values[0]) == 1, f"Expected 1 {parameter}, got '{result_values[0]}'"

        return result_values[0][0]


def _get_service_name(db):
    return _get_single_context_parameter(db, "SERVICE_NAME")


def _get_system_identifier(db):
    return _get_single_context_parameter(db, "INSTANCE_NAME")


@pytest.fixture()
def cleanup(request):
    """Provide an ExitStack to manage cleanup function execution."""
    try:
        with ExitStack() as exit_stack:
            yield exit_stack
    except Exception as exception:
        logger.warning(f"Error during cleanup of {request.node.name}: {exception}")


@pytest.fixture()
def test_name(request):
    """Returns the parametrized name of the test requesting the fixture."""
    return f"{request.node.name}"


def current_scn(db):
    try:
        connection = db.engine.connect()
        return int(str(connection.execute("SELECT CURRENT_SCN FROM V$DATABASE").first()[0]))
    except Exception:
        raise Exception("Error retrieving last SCN from Oracle database.")


@database("oracle")
@pytest.fixture(scope="module", autouse=True)
def util_setup(database):
    """Must be imported in order to use fixtures that are dependent on this one."""
    global SERVICE_NAME, SYSTEM_IDENTIFIER
    SERVICE_NAME = _get_service_name(database)
    SYSTEM_IDENTIFIER = _get_system_identifier(database)


@pytest.fixture()
def service_name(util_setup):
    """Requires importing util_setup."""
    return SERVICE_NAME


@pytest.fixture()
def system_identifier(util_setup):
    """Requires importing util_setup."""
    return SYSTEM_IDENTIFIER


class Parameters(ABC):
    """A set of parameters set as Oracle CDC Origin attributes."""

    @abstractmethod
    def as_dict(self):
        pass

    def __add__(self, other):
        """Merge two Parameters. The one on the right has preference over
        conflicting items."""
        if isinstance(other, Parameters):
            return RawParameters({**self.as_dict(), **other.as_dict()})
        return RawParameters({**self.as_dict(), **other})

    def __or__(self, other):
        """Merge two Parameters. The one on the right has preference over
        conflicting items."""
        # Starting in Python 3.9 dictionaries are merged with the OR operator, e.g. x | y
        # By using the OR operator we gain consistency between types.
        return self.__add__(other)

    def __getitem__(self, item):
        """Required to be used as kwargs with the ** operator."""
        return self.as_dict()[item]

    def keys(self):
        """Required to be used as kwargs with the ** operator."""
        return self.as_dict().keys()


class RawParameters(Parameters):
    """Empty canvas to fill with any dictionary."""

    def __init__(self, parameter_dict):
        self.parameter_dict = parameter_dict

    def as_dict(self):
        return self.parameter_dict


class DefaultConnectionParameters(Parameters):
    """Connect via service name."""

    service_name = None

    def __init__(self, db):
        self.database = db
        if self.service_name is None:
            self.service_name = _get_service_name(self.database)

    def as_dict(self):
        return {
            "host": self.database.host,
            "port": self.database.port,
            "service_name": self.service_name,
            "username": self.database.username,
            "password": self.database.password,
        }


class DefaultTableParameters(Parameters):
    """Filter a single table."""

    def __init__(self, table_name):
        self.table_name = table_name

    def as_dict(self):
        return {"tables_filter": [{"tablesInclusionPattern": self.table_name}]}


class DefaultStartParameters(Parameters):
    """Start with the last SCN at the moment of evaluating these parameters."""

    def __init__(self, db):
        self.database = db

    def as_dict(self):
        return {"start_mode": "CHANGE", "initial_system_change_number": current_scn(self.database)}
