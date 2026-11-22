"""XML feed in the otwarte-dane harvester format (XSD 1.13) so an MCOD "Data source" can import the AED register.

MCOD requires: a URL ending in .xml, a sibling .md5 file, an XML content-type, HEAD support
and XSD validity. main.py serves /aed-feed.xml and /aed-feed.md5 from build_feed().
"""

from hashlib import md5
from xml.sax.saxutils import escape

NAMESPACE = "urn:otwarte-dane:harvester:1.13"
DATASET_ID = "aed-register"  # extIdent, max 36 chars; keeps re-imports updating the same dataset
RESOURCE_ID = "aed-register-csv"


def build_feed(csv_url: str, total: int) -> bytes:
    title = "Rejestr defibrylatorów AED"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ns2:datasets xmlns:ns2="{NAMESPACE}">
    <dataset status="published">
        <extIdent>{DATASET_ID}</extIdent>
        <title>
            <polish>{title}</polish>
        </title>
        <description>
            <polish>Ogólnopolski rejestr lokalizacji defibrylatorów AED zbierany od dysponentów i zgłaszany przez mieszkańców. Liczba lokalizacji: {total}.</polish>
        </description>
        <updateFrequency>daily</updateFrequency>
        <categories>
            <category>HEAL</category>
        </categories>
        <resources>
            <resource>
                <extIdent>{RESOURCE_ID}</extIdent>
                <url>{escape(csv_url)}</url>
                <title>
                    <polish>Lokalizacje defibrylatorów AED</polish>
                </title>
                <description>
                    <polish>Nazwa obiektu, adres, współrzędne, dostępność, status i dysponent każdego defibrylatora.</polish>
                </description>
            </resource>
        </resources>
        <tags>
            <tag lang="pl">AED</tag>
            <tag lang="pl">defibrylator</tag>
            <tag lang="pl">zdrowie</tag>
        </tags>
    </dataset>
</ns2:datasets>
""".encode("utf-8")


def feed_md5(content: bytes) -> str:
    return md5(content).hexdigest()
