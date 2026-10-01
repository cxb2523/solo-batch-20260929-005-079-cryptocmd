#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Exporter layer: CSV / tablib formats / DataFrame / standalone HTML output.
"""

import csv
import html

import tablib


class Exporter(object):
    """Exports header/row tables to various output formats."""

    def to_csv_file(self, headers, rows, file_path):
        """Write rows to a csv file (no pandas required)."""
        with open(file_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile, delimiter=",", quoting=csv.QUOTE_NONNUMERIC)
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)

    def to_format(self, format, headers, rows):
        """
        Export rows via tablib.
        :param format: extension name of data format. Available: json, xls, yaml, csv, dbf, tsv, html, latex, xlsx, ods
        :return: exported data (str or bytes depending on the format).
        """
        data = tablib.Dataset()
        data.headers = headers
        for row in rows:
            data.append(row)
        return data.export(format)

    @staticmethod
    def require_pandas():
        """
        Import pandas lazily, raising NotImplementedError when it is not
        installed. pandas is only required for DataFrame output.
        """
        try:
            import pandas as pd
        except ImportError:
            pd = None

        if pd is None:
            raise NotImplementedError(
                "DataFrame Format requires 'pandas' to be installed."
                "Try : pip install pandas"
            )
        return pd

    def to_dataframe(self, headers, rows, date_as_index=False):
        """
        Build a pandas DataFrame. pandas is imported lazily and is only
        required when this method is called.
        """
        pd = self.require_pandas()

        dataframe = pd.DataFrame(data=rows, columns=headers)

        # convert 'Date' column to datetime type
        dataframe["Date"] = pd.to_datetime(
            dataframe["Date"], format="%d-%m-%Y", dayfirst=True
        )

        if date_as_index:
            # set 'Date' column as index and drop the the 'Date' column.
            dataframe.set_index("Date", inplace=True)

        return dataframe

    def to_html(self, headers, rows, title="Cryptocurrency market data"):
        """
        Render rows as a standalone HTML page (inline styles, no external
        dependencies) that can be opened directly in a browser.
        :return: HTML document as a string.
        """
        head_cells = "".join(
            "<th>{}</th>".format(html.escape(str(h))) for h in headers
        )
        body_rows = []
        for row in rows:
            cells = "".join(
                "<td>{}</td>".format(html.escape(str(value))) for value in row
            )
            body_rows.append("            <tr>{}</tr>".format(cells))
        return _HTML_TEMPLATE.format(
            title=html.escape(str(title)),
            head_cells=head_cells,
            body_rows="\n".join(body_rows),
        )

    def to_html_file(
        self, headers, rows, file_path, title="Cryptocurrency market data"
    ):
        """Write the standalone HTML page to a file."""
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(self.to_html(headers, rows, title=title))


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{title}</title>
    <style>
      body {{ font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
             margin: 2rem; color: #1a1a1a; }}
      h1 {{ font-size: 1.25rem; }}
      table {{ border-collapse: collapse; width: 100%; font-size: 0.875rem; }}
      th, td {{ border: 1px solid #d0d0d0; padding: 0.4rem 0.6rem; text-align: right; }}
      th {{ background: #f4f4f4; position: sticky; top: 0; }}
      tbody tr:nth-child(even) {{ background: #fafafa; }}
      td:first-child, th:first-child {{ text-align: left; }}
    </style>
  </head>
  <body>
    <h1>{title}</h1>
    <table>
      <thead>
        <tr>{head_cells}</tr>
      </thead>
      <tbody>
{body_rows}
      </tbody>
    </table>
  </body>
</html>
"""
