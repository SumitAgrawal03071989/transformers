import json
import sys
import os
from abc import ABC, abstractmethod

import google as google
import requests.exceptions
from google.api_core.exceptions import BadRequest, Forbidden
from google.api_core.retry import if_exception_type, if_transient_error
from google.cloud import bigquery
from google.cloud.bigquery.job import QueryJobConfig, CreateDisposition
from google.cloud.bigquery.schema import _parse_schema_resource
from google.cloud.bigquery.table import TimePartitioningType, TimePartitioning, TableReference, Table
from google.cloud.exceptions import GoogleCloudError

from bumblebee.config import TaskConfigFromEnv
from bumblebee.log import get_logger

logger = get_logger(__name__)

SERVICE_ACCOUNT_VAR = "BQ_SERVICE_ACCOUNT"
SERVICE_ACCOUNT_TYPE = "service_account"


class BaseBigqueryService(ABC):

    @abstractmethod
    def execute_query(self, query):
        pass

    @abstractmethod
    def transform_load(self,
                       query,
                       source_project_id=None,
                       destination_table=None,
                       write_disposition=None,
                       create_disposition=CreateDisposition.CREATE_NEVER,
                       allow_field_addition=False):
        pass

    @abstractmethod
    def create_table(self, full_table_name, schema_file,
                     partitioning_type=TimePartitioningType.DAY,
                     partitioning_field=None):
        pass

    @abstractmethod
    def delete_table(self, full_table_name):
        pass

    @abstractmethod
    def get_table(self, full_table_name):
        pass

def if_exception_funcs(fn_origin, fn_additional):
    def if_exception_func_predicate(exception):
        return fn_origin(exception) or fn_additional(exception)
    return if_exception_func_predicate

class BigqueryService(BaseBigqueryService):

    def __init__(self, client, labels, writer, retry_timeout = None, on_job_finish = None, on_job_register = None):
        """

        :rtype:
        """
        self.client = client
        self.labels = labels
        self.writer = writer
        if_additional_transient_error = if_exception_type(
            requests.exceptions.Timeout,
            requests.exceptions.SSLError,
        )
        predicate = if_exception_funcs(if_transient_error, if_additional_transient_error)
        retry = bigquery.DEFAULT_RETRY.with_deadline(retry_timeout) if retry_timeout else bigquery.DEFAULT_RETRY
        self.retry = retry.with_predicate(predicate)
        self.on_job_finish = on_job_finish
        self.on_job_register = on_job_register

    def execute_query(self, query):
        query_job_config = QueryJobConfig()
        query_job_config.use_legacy_sql = False
        query_job_config.labels = self.labels

        if query is None or len(query) == 0:
            raise ValueError("query must not be Empty")

        logger.info("executing query")
        query_job = self.client.query(query=query,
                                      job_config=query_job_config,
                                      retry=self.retry)
        logger.info("Job {} is initially in state {} of {} project".format(query_job.job_id, query_job.state,
                                                                           query_job.project))

        if self.on_job_register:
            self.on_job_register(self.client, query_job)

        try:
            result = query_job.result()
        except (GoogleCloudError, Forbidden, BadRequest) as ex:
            self.writer.write("error", ex.message)
            logger.error(ex)
            sys.exit(1)

        logger.info("Job {} is finally in state {} of {} project".format(query_job.job_id, query_job.state,
                                                                         query_job.project))
        logger.info("Bytes processed: {}, Affected Rows: {}, Bytes billed: {}".format(query_job.estimated_bytes_processed,
                                                               query_job.num_dml_affected_rows,
                                                               query_job.total_bytes_billed))
        logger.info("Job labels {}".format(query_job._configuration.labels))

        if self.on_job_finish is not None:
            self.on_job_finish(query_job)
        return result

    def transform_load(self,
                       query,
                       source_project_id=None,
                       destination_table=None,
                       write_disposition=None,
                       create_disposition=CreateDisposition.CREATE_NEVER,
                       allow_field_addition=False):
        if query is None or len(query) == 0:
            raise ValueError("query must not be Empty")

        query_job_config = QueryJobConfig()
        query_job_config.create_disposition = create_disposition
        query_job_config.write_disposition = write_disposition
        query_job_config.use_legacy_sql = False
        query_job_config.labels = self.labels
        if allow_field_addition:
            query_job_config.schema_update_options = [
                bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION,
                bigquery.SchemaUpdateOption.ALLOW_FIELD_RELAXATION
            ]

        if destination_table is not None:
            table_ref = TableReference.from_string(destination_table)
            query_job_config.destination = table_ref

        logger.info("transform load")
        query_job = self.client.query(query=query,
                                      job_config=query_job_config,
                                      retry=self.retry)
        logger.info("Job {} is initially in state {} of {} project".format(query_job.job_id, query_job.state,
                                                                           query_job.project))

        if self.on_job_register:
            self.on_job_register(self.client, query_job)

        try:
            result = query_job.result()
        except (GoogleCloudError, Forbidden, BadRequest) as ex:
            self.writer.write("error", ex.message)
            logger.error(ex)
            sys.exit(1)

        logger.info("Job {} is finally in state {} of {} project".format(query_job.job_id, query_job.state,
                                                                           query_job.project))
        logger.info("Bytes processed: {}, Stats: {} {}".format(query_job.estimated_bytes_processed,
                                                               query_job.num_dml_affected_rows,
                                                               query_job.total_bytes_billed))
        logger.info("Job labels {}".format(query_job._configuration.labels))

        if self.on_job_finish is not None:
            self.on_job_finish(query_job)
        return result

    def create_table(self, full_table_name, schema_file,
                     partitioning_type=TimePartitioningType.DAY,
                     partitioning_field=None):
        with open(schema_file, 'r') as file:
            schema_json = json.load(file)
        table_schema = _parse_schema_resource({'fields': schema_json})

        table_ref = TableReference.from_string(full_table_name)

        bigquery_table = bigquery.Table(table_ref, table_schema)
        bigquery_table.time_partitioning = TimePartitioning(type_=partitioning_type,
                                                            field=partitioning_field)

        self.client.create_table(bigquery_table)

    def delete_table(self, full_table_name):
        table_ref = TableReference.from_string(full_table_name)
        self.client.delete_table(bigquery.Table(table_ref))

    def get_table(self, full_table_name):
        table_ref = TableReference.from_string(full_table_name)
        return self.client.get_table(table_ref)


def create_bigquery_service(task_config: TaskConfigFromEnv, labels, writer, on_job_finish = None, on_job_register = None):
    if writer is None:
        writer = writer.StdWriter()

    credentials = _get_bigquery_credentials()
    default_query_job_config = QueryJobConfig()
    default_query_job_config.priority = task_config.query_priority
    default_query_job_config.allow_field_addition = task_config.allow_field_addition
    client = bigquery.Client(project=task_config.execution_project, credentials=credentials, default_query_job_config=default_query_job_config)
    return BigqueryService(client, labels, writer, retry_timeout=task_config.retry_timeout, on_job_finish=on_job_finish, on_job_register=on_job_register)


def _get_bigquery_credentials():
    """Gets credentials from the BQ_SERVICE_ACCOUNT environment var else GOOGLE_APPLICATION_CREDENTIALS for file path."""
    scope = ('https://www.googleapis.com/auth/bigquery',
             'https://www.googleapis.com/auth/cloud-platform',
             'https://www.googleapis.com/auth/drive')
    account = os.environ.get(SERVICE_ACCOUNT_VAR)
    creds = _load_credentials_from_var(account, scope)
    if creds is not None:
        return creds
    credentials, _ = google.auth.default(scopes=scope)
    return credentials


def _load_credentials_from_var(account_str, scopes=None):
    """Loads Google credentials from an environment variable.
    The credentials file must be a service account key.
    """
    if account_str is None:
        return None

    try:
        info = json.loads(account_str)
    except ValueError:
        return None

    # The type key should indicate that the file is either a service account
    # credentials file or an authorized user credentials file.
    credential_type = info.get("type")

    if credential_type == SERVICE_ACCOUNT_TYPE:
        from google.oauth2 import service_account

        try:
            credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
        except ValueError:
            return None
        return credentials

    else:
        return None


class DummyService(BaseBigqueryService):

    def execute_query(self, query):
        logger.info("execute query : {}".format(query))
        return []

    def transform_load(self, query, source_project_id=None, destination_table=None, write_disposition=None,
                       create_disposition=CreateDisposition.CREATE_NEVER, allow_field_addition=False):
        log = """ transform and load with config :
        {}
        {}
        {}
        {}""".format(query, source_project_id, destination_table, write_disposition)
        logger.info(log)

    def create_table(self, full_table_name, schema_file, partitioning_type=TimePartitioningType.DAY,
                     partitioning_field=None):
        log = """ create table with config :
        {}
        {}
        {}
        {}""".format(full_table_name, schema_file, partitioning_type, partitioning_field)
        logger.info(log)

    def delete_table(self, full_table_name):
        logger.info("delete table: {}".format(full_table_name))

    def get_table(self, full_table_name):
        return Table.from_string(full_table_name)
