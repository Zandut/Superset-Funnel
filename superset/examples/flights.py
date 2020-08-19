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
import pandas as pd
from sqlalchemy import DateTime

from superset import db
from superset.utils import core as utils
from .helpers import get_example_data, TBL


def load_flights(only_metadata=False, force=False):
    """Loading random time series data from a zip file in the repo"""
    tbl_name = "flights"
    database = utils.get_example_database()
    table_exists = database.has_table_by_name(tbl_name)

    if not only_metadata and (not table_exists or force):
        data = get_example_data("flight_data.csv.gz", make_bytes=True)
        pdf = pd.read_csv(data, encoding="latin-1")

        # Loading airports info to join and get lat/long
        airports_bytes = get_example_data("airports.csv.gz", make_bytes=True)
        airports = pd.read_csv(airports_bytes, encoding="latin-1")
        airports = airports.set_index("IATA_CODE")

        pdf["ds"] = (
            pdf.YEAR.map(str) + "-0" + pdf.MONTH.map(str) + "-0" + pdf.DAY.map(str)
        )
        pdf.ds = pd.to_datetime(pdf.ds)
        del pdf["YEAR"]
        del pdf["MONTH"]
        del pdf["DAY"]

        pdf = pdf.join(airports, on="ORIGIN_AIRPORT", rsuffix="_ORIG")
        pdf = pdf.join(airports, on="DESTINATION_AIRPORT", rsuffix="_DEST")
        pdf.to_sql(
            tbl_name,
            database.get_sqla_engine(),
            if_exists="replace",
            chunksize=500,
            dtype={"ds": DateTime},
            index=False,
        )

    tbl = db.session.query(TBL).filter_by(table_name=tbl_name).first()
    if not tbl:
        tbl = TBL(table_name=tbl_name)
    tbl.description = "Random set of flights in the US"
    tbl.database = database
    db.session.merge(tbl)
    db.session.commit()
    tbl.fetch_metadata()
    print("Done loading table!")
