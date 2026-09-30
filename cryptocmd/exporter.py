#!/usr/bin/env python

"""
Output layer for scraped CoinMarketCap tables.

The :class:`Exporter` turns plain header/row tables into CSV files, tablib
formats, pandas DataFrames (lazily imported) and a standalone HTML page.
Every public return is a built-in type (``str``/``bytes``/DataFrame); no
internal object ever leaks out.
"""

import csv
import html as html_lib
from io import StringIO


class Exporter:
    """Render and persist header/row tables in several formats."""

    # ------------------------------------------------------------------
    # CSV -- hand written with the stdlib so pandas is never required.
    # Uses the default csv dialect, which is exactly what tablib's CSV
    # exporter uses (QUOTE_MINIMAL, CRLF terminators).
    # ------------------------------------------------------------------

    @staticmethod
    def csv_text(headers, rows):
        """Return the table as a CSV ``str`` (identical to tablib output)."""
        stream = StringIO()
        writer = csv.writer(stream, delimiter=",")
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        return stream.getvalue()

    def write_csv(self, path, headers, rows):
        """Write the table to ``path`` as UTF-8 CSV bytes."""
        data = self.csv_text(headers, rows).encode("utf-8")
        with open(path, "wb") as fp:
            fp.write(data)
        return path

    def write_csv_legacy(self, path, headers, rows):
        """Byte-for-byte compatible with the deprecated ``export_csv`` path.

        It used ``csv.QUOTE_NONNUMERIC`` via a text file with ``newline=''``.
        """
        with open(path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile, delimiter=",", quoting=csv.QUOTE_NONNUMERIC)
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)
        return path

    # ------------------------------------------------------------------
    # tablib formats (json, xls, yaml, tsv, html, xlsx, ods, ...)
    # ------------------------------------------------------------------

    @staticmethod
    def export_format(fmt, headers, rows):
        """Return the table rendered by tablib in the requested ``fmt``."""
        import tablib

        dataset = tablib.Dataset()
        dataset.headers = headers
        for row in rows:
            dataset.append(row)
        return dataset.export(fmt)

    # ------------------------------------------------------------------
    # pandas -- imported lazily, only when actually requested
    # ------------------------------------------------------------------

    @staticmethod
    def to_pandas(headers, rows, date_as_index=False):
        """Return a pandas DataFrame of the table.

        Raises ``NotImplementedError`` with the original message when pandas
        is not installed.
        """
        try:
            import pandas as pd
        except ImportError:
            raise NotImplementedError(
                "DataFrame Format requires 'pandas' to be installed."
                "Try : pip install pandas"
            )

        dataframe = pd.DataFrame(data=rows, columns=headers)

        # convert 'Date' column to datetime type
        dataframe["Date"] = pd.to_datetime(
            dataframe["Date"], format="%d-%m-%Y", dayfirst=True
        )

        if date_as_index:
            # set 'Date' column as index and drop the the 'Date' column.
            dataframe.set_index("Date", inplace=True)

        return dataframe

    # ------------------------------------------------------------------
    # Standalone HTML page -- openable directly in a browser
    # ------------------------------------------------------------------

    def html_text(self, headers, rows, title="Crypto Market Data"):
        """Return a self-contained HTML page string for the table."""
        head_cells = "".join(
            f"<th>{html_lib.escape(str(header))}</th>" for header in headers
        )
        body_rows = []
        for row in rows:
            cells = "".join(
                "<td>{}</td>".format(
                    html_lib.escape("" if value is None else str(value))
                )
                for value in row
            )
            body_rows.append(f"<tr>{cells}</tr>")
        body = "\n      ".join(body_rows)
        return (
            "<!DOCTYPE html>\n"
            '<html lang="en">\n'
            "<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"<title>{html_lib.escape(str(title))}</title>\n"
            "<style>\n"
            "body{font-family:Arial,Helvetica,sans-serif;margin:2rem;}\n"
            "h1{font-size:1.4rem;}\n"
            "table{border-collapse:collapse;min-width:480px;}\n"
            "th,td{border:1px solid #ccc;padding:6px 10px;text-align:right;}\n"
            "th{background:#f2f2f2;}\n"
            "td:first-child,th:first-child{text-align:left;}\n"
            "tr:nth-child(even){background:#fafafa;}\n"
            "</style>\n"
            "</head>\n"
            "<body>\n"
            f"<h1>{html_lib.escape(str(title))}</h1>\n"
            "<table>\n"
            "<thead>\n"
            f"      <tr>{head_cells}</tr>\n"
            "</thead>\n"
            "<tbody>\n"
            f"      {body}\n"
            "</tbody>\n"
            "</table>\n"
            "</body>\n"
            "</html>\n"
        )

    def write_html(self, path, headers, rows, title="Crypto Market Data"):
        """Write a self-contained UTF-8 HTML page to ``path``."""
        data = self.html_text(headers, rows, title=title).encode("utf-8")
        with open(path, "wb") as fp:
            fp.write(data)
        return path

    # ------------------------------------------------------------------
    # Generic file writing helper (mirrors the legacy export() behaviour)
    # ------------------------------------------------------------------

    @staticmethod
    def write_bytes_or_text(path, data):
        """Write ``str`` (utf-8) or ``bytes`` to ``path``."""
        with open(path, "wb") as fp:
            if isinstance(data, str):
                fp.write(data.encode("utf-8"))
            else:
                fp.write(data)
        return path

