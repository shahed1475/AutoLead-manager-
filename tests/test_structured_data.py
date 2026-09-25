"""schema.org structured data (JSON-LD) read off a business's website."""
from backend.enrichment import structured_data as sd

GRAPH = """
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": ["Dentist", "LocalBusiness"], "@id": "#org", "name": "Smile Studio",
   "telephone": "+44 20 7946 0000", "email": "mailto:Hello@SmileStudio.co.uk",
   "sameAs": ["https://facebook.com/smilestudio", "https://www.instagram.com/smilestudio/"],
   "address": {"@type": "PostalAddress", "streetAddress": "1 High St", "addressLocality": "London",
               "postalCode": "W1 1AA", "addressCountry": "GB"},
   "founder": {"@id": "#jane"},
   "employee": [{"@type": "Person", "name": "Dr Omar Aziz", "jobTitle": "Principal Dentist",
                 "email": "omar@smilestudio.co.uk"}]},
  {"@type": "Person", "@id": "#jane", "name": "Jane Porter", "jobTitle": "Founder & CEO",
   "telephone": "+44 7700 900123"},
  {"@type": "Person", "@id": "#author", "name": "admin"},
  {"@type": "WebPage", "author": {"@id": "#author"}}
]}
</script>
<script type="application/ld+json">{ this is not json </script>
"""


def test_reads_business_people_and_contacts():
    r = sd.extract(GRAPH)
    assert r["name"] == "Smile Studio"
    assert r["types"] == ["Dentist", "LocalBusiness"]
    assert r["telephones"] == ["+44 20 7946 0000"]
    assert r["emails"] == ["hello@smilestudio.co.uk"]
    assert r["same_as"] == ["https://facebook.com/smilestudio", "https://www.instagram.com/smilestudio/"]
    assert r["address"] == "1 High St, London, W1 1AA, GB"
    people = {p["name"]: p for p in r["people"]}
    assert set(people) == {"Jane Porter", "Dr Omar Aziz"}            # the page author "admin" is not staff
    assert people["Jane Porter"]["role"] == "founder" and people["Jane Porter"]["job_title"] == "Founder & CEO"
    assert people["Jane Porter"]["telephone"] == "+44 7700 900123"
    assert people["Dr Omar Aziz"]["email"] == "omar@smilestudio.co.uk"


def test_standalone_person_needs_a_job_title():
    html = ('<script type="application/ld+json">[{"@type":"Person","name":"Blog Writer"},'
            '{"@type":"Person","name":"Ana Ruiz","jobTitle":"Owner"}]</script>')
    assert [p["name"] for p in sd.extract(html)["people"]] == ["Ana Ruiz"]


def test_nothing_to_read():
    empty = sd.extract("<html><body>Hi</body></html>")
    assert empty == {"name": None, "types": [], "telephones": [], "emails": [], "same_as": [],
                     "address": None, "people": []}
    assert sd.extract(None)["people"] == []


def test_several_job_titles_are_joined_readably():
    html = ('<script type="application/ld+json">{"@type":"Person","name":"Dr S",'
            '"jobTitle":["Laser Dentist","Periodontist"]}</script>')
    assert sd.extract(html)["people"][0]["job_title"] == "Laser Dentist, Periodontist"
