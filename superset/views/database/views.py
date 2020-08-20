 # Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
# pylint: disable=C,R,W
import os
import tempfile
from flask import flash, redirect, g
from flask_appbuilder import SimpleFormView
from flask_appbuilder.forms import DynamicForm
from flask_appbuilder.models.sqla.interface import SQLAInterface
from flask_babel import gettext as __
from flask_babel import lazy_gettext as _
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename
from wtforms.fields import StringField
from wtforms.validators import ValidationError
from superset.sql_parse import Table

from superset import app, appbuilder, security_manager, db
from superset.connectors.sqla.models import SqlaTable
import superset.models.core as models
from superset.utils import core as utils
from superset.views.base import DeleteMixin, SupersetModelView, YamlExportMixin
from . import DatabaseMixin, sqlalchemy_uri_validator
from .forms import CsvToDatabaseForm, ExcelToDatabaseForm


config = app.config
stats_logger = config.get("STATS_LOGGER")


def sqlalchemy_uri_form_validator(form: DynamicForm, field: StringField) -> None:
    """
        Check if user has submitted a valid SQLAlchemy URI
    """
    sqlalchemy_uri_validator(field.data, exception=ValidationError)

def upload_stream_write(form_file_field: "FileStorage", path: str) -> None:
    chunk_size = app.config["UPLOAD_CHUNK_SIZE"]
    with open(path, "bw") as file_description:
        while True:
            chunk = form_file_field.stream.read(chunk_size)
            if not chunk:
                break
            file_description.write(chunk)

class DatabaseView(
    DatabaseMixin, SupersetModelView, DeleteMixin, YamlExportMixin
):  # noqa
    datamodel = SQLAInterface(models.Database)

    add_template = "superset/models/database/add.html"
    edit_template = "superset/models/database/edit.html"
    validators_columns = {"sqlalchemy_uri": [sqlalchemy_uri_form_validator]}

    def _delete(self, pk):
        DeleteMixin._delete(self, pk)


appbuilder.add_link(
    "Import Dashboards",
    label=__("Import Dashboards"),
    href="/metrix/import_dashboards",
    icon="fa-cloud-upload",
    category="Manage",
    category_label=__("Manage"),
    category_icon="fa-wrench",
)


appbuilder.add_view(
    DatabaseView,
    "Databases",
    label=__("Databases"),
    icon="fa-database",
    category="Sources",
    category_label=__("Sources"),
    category_icon="fa-database",
)


class CsvToDatabaseView(SimpleFormView):
    form = CsvToDatabaseForm
    form_template = "superset/form_view/csv_to_database_view/edit.html"
    form_title = _("CSV to Database configuration")
    add_columns = ["database", "schema", "table_name"]

    def form_get(self, form):
        form.sep.data = ","
        form.header.data = 0
        form.mangle_dupe_cols.data = True
        form.skipinitialspace.data = False
        form.skip_blank_lines.data = True
        form.infer_datetime_format.data = True
        form.decimal.data = "."
        form.if_exists.data = "fail"

    def form_post(self, form):
        database = form.con.data
        schema_name = form.schema.data or ""

        if not self.is_schema_allowed(database, schema_name):
            message = _(
                'Database "{0}" Schema "{1}" is not allowed for csv uploads. '
                "Please contact Superset Admin".format(
                    database.database_name, schema_name
                )
            )
            flash(message, "danger")
            return redirect("/csvtodatabaseview/form")

        csv_file = form.csv_file.data
        form.csv_file.data.filename = secure_filename(form.csv_file.data.filename)
        csv_filename = form.csv_file.data.filename
        path = os.path.join(config["UPLOAD_FOLDER"], csv_filename)
        try:
            utils.ensure_path_exists(config["UPLOAD_FOLDER"])
            csv_file.save(path)
            table = SqlaTable(table_name=form.name.data)
            table.database = form.data.get("con")
            table.database_id = table.database.id
            table.database.db_engine_spec.create_table_from_csv(form, table)
        except Exception as e:
            try:
                os.remove(path)
            except OSError:
                pass
            message = (
                "Table name {} already exists. Please pick another".format(
                    form.name.data
                )
                if isinstance(e, IntegrityError)
                else str(e)
            )
            flash(message, "danger")
            stats_logger.incr("failed_csv_upload")
            return redirect("/csvtodatabaseview/form")

        os.remove(path)
        # Go back to welcome page / splash screen
        db_name = table.database.database_name
        message = _(
            'CSV file "{0}" uploaded to table "{1}" in '
            'database "{2}"'.format(csv_filename, form.name.data, db_name)
        )
        flash(message, "info")
        stats_logger.incr("successful_csv_upload")
        return redirect("/tablemodelview/list/")

    def is_schema_allowed(self, database, schema):
        if not database.allow_csv_upload:
            return False
        schemas = database.get_schema_access_for_csv_upload()
        if schemas:
            return schema in schemas
        return (
            security_manager.database_access(database)
            or security_manager.all_datasource_access()
        )


appbuilder.add_view_no_menu(CsvToDatabaseView)

class ExcelToDatabaseView(SimpleFormView):
    form = ExcelToDatabaseForm
    form_template = "superset/form_view/excel_to_database_view/edit.html"
    form_title = _("Excel to Database configuration")
    add_columns = ["database", "schema", "table_name"]

    def form_get(self, form):
        form.header.data = 0
        form.mangle_dupe_cols.data = True
        form.skipinitialspace.data = False
        form.decimal.data = "."
        form.if_exists.data = "fail"
        form.sheet_name = None

    def form_post(self, form):
        database = form.con.data
        excel_table = Table(table=form.name.data, schema=form.schema.data)

        if not self.is_schema_allowed(database, excel_table.schema):
            message = _(
                'Database "%(database_name)s" schema "%(schema_name)s" '
                "is not allowed for excel uploads. Please contact your Superset Admin.",
                database_name=database.database_name,
                schema_name=excel_table.schema,
            )
            flash(message, "danger")
            return redirect("/exceltodatabaseview/form")

        if "." in excel_table.table and excel_table.schema:
            message = _(
                "You cannot specify a namespace both in the name of the table: "
                '"%(excel_table.table)s" and in the schema field: '
                '"%(excel_table.schema)s". Please remove one',
                table=excel_table.table,
                schema=excel_table.schema,
            )
            flash(message, "danger")
            return redirect("/exceltodatabaseview/form")

        uploaded_tmp_file_path = tempfile.NamedTemporaryFile(
            dir=app.config["UPLOAD_FOLDER"],
            suffix=os.path.splitext(form.excel_file.data.filename)[1].lower(),
            delete=False,
        ).name

        try:
            utils.ensure_path_exists(config["UPLOAD_FOLDER"])
            upload_stream_write(form.excel_file.data, uploaded_tmp_file_path)

            con = form.data.get("con")
            database = (
                db.session.query(models.Database).filter_by(id=con.data.get("id")).one()
            )
            excel_to_df_kwargs = {
                "header": form.header.data if form.header.data else 0,
                "index_col": form.index_col.data,
                "mangle_dupe_cols": form.mangle_dupe_cols.data,
                "skipinitialspace": form.skipinitialspace.data,
                "skiprows": form.skiprows.data,
                "nrows": form.nrows.data,
                "sheet_name": form.sheet_name.data,
                "chunksize": 1000,
            }
            df_to_sql_kwargs = {
                "name": excel_table.table,
                "if_exists": form.if_exists.data,
                "index": form.index.data,
                "index_label": form.index_label.data,
                "chunksize": 1000,
            }
            database.db_engine_spec.create_table_from_excel(
                uploaded_tmp_file_path,
                excel_table,
                database,
                excel_to_df_kwargs,
                df_to_sql_kwargs,
            )

            # Connect table to the database that should be used for exploration.
            # E.g. if hive was used to upload a excel, presto will be a better option
            # to explore the table.
            expore_database = database
            explore_database_id = database.get_extra().get("explore_database_id", None)
            if explore_database_id:
                expore_database = (
                    db.session.query(models.Database)
                    .filter_by(id=explore_database_id)
                    .one_or_none()
                    or database
                )

            sqla_table = (
                db.session.query(SqlaTable)
                .filter_by(
                    table_name=excel_table.table,
                    schema=excel_table.schema,
                    database_id=expore_database.id,
                )
                .one_or_none()
            )

            if sqla_table:
                sqla_table.fetch_metadata()
            if not sqla_table:
                sqla_table = SqlaTable(table_name=excel_table.table)
                sqla_table.database = expore_database
                sqla_table.database_id = database.id
                sqla_table.user_id = g.user.id
                sqla_table.schema = excel_table.schema
                sqla_table.fetch_metadata()
                db.session.add(sqla_table)
            db.session.commit()
        except Exception as ex:  # pylint: disable=broad-except
            db.session.rollback()
            try:
                os.remove(uploaded_tmp_file_path)
            except OSError:
                pass
            message = _(
                'Unable to upload Excel file "%(filename)s" to table '
                '"%(table_name)s" in database "%(db_name)s". '
                "Error message: %(error_msg)s",
                filename=form.excel_file.data.filename,
                table_name=form.name.data,
                db_name=database.database_name,
                error_msg=str(ex),
            )

            flash(message, "danger")
            stats_logger.incr("failed_excel_upload")
            return redirect("/exceltodatabaseview/form")

        os.remove(uploaded_tmp_file_path)
        # Go back to welcome page / splash screen
        message = _(
            'CSV file "%(excel_filename)s" uploaded to table "%(table_name)s" in '
            'database "%(db_name)s"',
            excel_filename=form.excel_file.data.filename,
            table_name=str(excel_table),
            db_name=sqla_table.database.database_name,
        )
        flash(message, "info")
        stats_logger.incr("successful_excel_upload")
        return redirect("/tablemodelview/list/")

    def is_schema_allowed(self, database, schema):
        if not database.allow_csv_upload:
            return False
        schemas = database.get_schema_access_for_csv_upload()
        if schemas:
            return schema in schemas
        return (
            security_manager.database_access(database)
            or security_manager.all_datasource_access()
        )

appbuilder.add_view_no_menu(ExcelToDatabaseView)

class DatabaseTablesAsync(DatabaseView):
    list_columns = ["id", "all_table_names_in_database", "all_schema_names"]


appbuilder.add_view_no_menu(DatabaseTablesAsync)


class DatabaseAsync(DatabaseView):
    list_columns = [
        "id",
        "database_name",
        "expose_in_sqllab",
        "allow_ctas",
        "force_ctas_schema",
        "allow_run_async",
        "allow_dml",
        "allow_multi_schema_metadata_fetch",
        "allow_csv_upload",
        "allows_subquery",
        "backend",
    ]


appbuilder.add_view_no_menu(DatabaseAsync)
