from huntbot2.discovery.linkedin import discover_from_search_html


def test_discover_from_search_html_parses_cards():
    html = """
    <div class='job-card'>
      <a class='job-link' href='/jobs/view/123'>Data Analyst</a>
      <span class='company'>Acme Corp</span>
      <span class='location'>Remote</span>
      <time class='posted'>1 day ago</time>
    </div>
    """
    jobs = discover_from_search_html(html, "https://www.linkedin.com/jobs/search")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "linkedin"
    assert job.title == "Data Analyst"
    assert job.company == "Acme Corp"
    assert job.location == "Remote"
    assert job.url == "https://www.linkedin.com/jobs/view/123/"
    assert job.posted_at == "1 day ago"


def test_discover_from_search_html_normalizes_and_skips_invalid_cards():
    html = """
    <div class='job-card'>
      <a class='job-link' href='/jobs/view/321?trk=abc'>  Data   Analyst </a>
      <span class='company'>  Acme   Corp </span>
      <span class='location'>  Remote  </span>
    </div>
    <div class='job-card'>
      <a class='job-link' href='/jobs/view/777'>   </a>
      <span class='company'>Nope</span>
    </div>
    """
    jobs = discover_from_search_html(html, "https://www.linkedin.com/jobs/search")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.title == "Data Analyst"
    assert job.company == "Acme Corp"
    assert job.location == "Remote"
    assert job.url == "https://www.linkedin.com/jobs/view/321/"
