import re
import xml.etree.ElementTree as ET
from hashlib import md5

import feed

NS = {"p": feed.NAMESPACE}


def parse(url="https://aed.example.org/dane-gov-export.csv?v=abc", total=74):
    content = feed.build_feed(url, total)
    return content, ET.fromstring(content)


def test_feed_declares_harvester_namespace_and_one_published_dataset():
    _, root = parse()
    assert root.tag == f"{{{feed.NAMESPACE}}}datasets"
    (dataset,) = root.findall("dataset")
    assert dataset.get("status") == "published"
    assert dataset.findtext("extIdent") == feed.DATASET_ID
    assert len(feed.DATASET_ID) <= 36 and len(feed.RESOURCE_ID) <= 36  # MCOD limit


def test_feed_resource_points_at_given_https_url_and_escapes_it():
    _, root = parse("https://aed.example.org/dane-gov-export.csv?a=1&b=2")
    (resource,) = root.findall("dataset/resources/resource")
    assert resource.findtext("url") == "https://aed.example.org/dane-gov-export.csv?a=1&b=2"
    assert resource.findtext("extIdent") == feed.RESOURCE_ID


def test_feed_mentions_row_count_and_has_required_metadata():
    _, root = parse(total=1234)
    dataset = root.find("dataset")
    assert "1234" in dataset.findtext("description/polish")
    assert dataset.findtext("updateFrequency") == "daily"
    assert dataset.findtext("categories/category") == "HEAL"
    assert dataset.findall("tags/tag")


def test_feed_is_deterministic_and_md5_matches():
    a, _ = parse()
    b, _ = parse()
    assert a == b  # MCOD fetches .xml and .md5 separately; they must agree
    assert feed.feed_md5(a) == md5(a).hexdigest() and re.fullmatch(r"[0-9a-f]{32}", feed.feed_md5(a))
