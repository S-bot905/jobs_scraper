# jobs_scraper.py
"""
Daily job scraper for Cloud & DevOps roles.
Searches: Indeed (India), Wellfound (AngelList), basic site searches for company job pages,
and generic Google site: queries (best-effort).
Filters: Remote OR India, 2-6 years experience.
Sends results via Gmail SMTP (use App Password).
"""

import os
import re
import smtplib
import time
import html
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

# ---------- Configuration ----------
GMAIL_USER = os.environ.get("GMAIL_USER")  # eesa18@gmail.com
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
EMAIL_TO = os.environ.get("EMAIL_TO", GMAIL_USER)
SENDER_NAME = os.environ.get("SENDER_NAME", "Daily Job Bot")

# Keywords to search for (default)
KEYWORDS = [
    "DevOps Engineer", "Cloud Engineer", "Site Reliability Engineer",
    "Platform Engineer", "Infrastructure Engineer", "AWS Engineer",
    "Azure DevOps Engineer", "Kubernetes Engineer"
]

# Search sources & helper functions
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

# Minimum and maximum years experience for filtering
MIN_YEARS = 2
MAX_YEARS = 6

# timeout & polite scraping
REQUEST_TIMEOUT = 15
SLEEP_BETWEEN_REQUESTS = 2.0


# ---------- Utilities ----------
def normalize_text(t):
    return " ".join(t.split()) if t else ""


def parse_experience_text(text):
    """
    Try to find expressions like '2-4 years', '3+ years', 'minimum 2 years', '2 years'
    Returns tuple (min_years, max_years_or_None)
    """
    if not text:
        return None, None

    text = text.lower()
    # Common patterns: "2-4 years"
    m = re.search(r'(\d{1,2})\s*[-–]\s*(\d{1,2})\s*years?', text)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Patterns like "3+ years" or "3 plus years"
    m = re.search(r'(\d{1,2})\s*\+\s*years?', text)
    if m:
        return int(m.group(1)), None
    m = re.search(r'minimum\s+of\s+(\d{1,2})\s*years?', text)
    if m:
        return int(m.group(1)), None
    m = re.search(r'(\d{1,2})\s*years?', text)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None, None


def experience_matches(min_y, max_y):
    """
    Decide if a job's experience requirement fits 2-6 years.
    Accept flexible matches (e.g., 2+, 3-5, 4 years)
    """
    if min_y is None and max_y is None:
        # No explicit requirement found -> consider it a match (conservative)
        return True
    if min_y is not None and max_y is not None:
        # Check overlap with desired range
        return not (max_y < MIN_YEARS or min_y > MAX_YEARS)
    if min_y is not None:
        # job says min N years
        return min_y <= MAX_YEARS
    # only max exists? (rare)
    if max_y is not None:
        return max_y >= MIN_YEARS
    return False


def location_matches(location_text):
    """
    Accept if remote or India mentioned.
    """
    if not location_text:
        return True
    t = location_text.lower()
    if "remote" in t or "india" in t or "india remote" in t or "pan india" in t:
        return True
    return False


def text_contains_keywords(text, keywords=KEYWORDS):
    t = (text or "").lower()
    for kw in keywords:
        if kw.lower() in t:
            return True
    # also accept 'devops' or 'cloud' as fallback
    if "devops" in t or "cloud" in t or "sre" in t or "site reliability" in t:
        return True
    return False


# ---------- Scrapers (best-effort) ----------
def scrape_indeed(query_kw="DevOps Engineer"):
    """
    Best-effort scrape Indeed India. This will fetch first result page for relevant query.
    """
    results = []
    q = "+".join(query_kw.split())
    url = f"https://in.indeed.com/jobs?q={q}+remote+cloud+devops&l=India"
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        soup = BeautifulSoup(r.text, "html.parser")
        cards = soup.select("a[data-jk], .job_seen_beacon a")
        for a in cards[:25]:
            link = a.get("href") or ""
            if link and link.startswith("/rc/"):
                link = "https://in.indeed.com" + link
            title = normalize_text(a.get_text())
            # find parent card for company/location snippet
            parent = a.find_parent()
            snippet = ""
            if parent:
                snippet = normalize_text(parent.get_text())
            results.append({
                "title": title,
                "company": None,
                "location": snippet,
                "link": link,
                "source": "Indeed",
                "snippet": snippet
            })
    except Exception as e:
        print("Indeed scrape error:", e)
    return results


def scrape_wellfound(query_kw="DevOps Engineer"):
    """
    Scrape Wellfound (AngelList). Public pages are accessible with query parameters.
    """
    results = []
    q = "+".join(query_kw.split())
    url = f"https://wellfound.com/jobs?search={q}&remote=true"
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        soup = BeautifulSoup(r.text, "html.parser")
        # Jobs are in <a data-test="JobCard"> anchors
        anchors = soup.select('a[href*="/jobs/"]')
        seen = set()
        for a in anchors:
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                link = "https://wellfound.com" + href
            else:
                link = href
            if link in seen:
                continue
            seen.add(link)
            title = normalize_text(a.get_text())
            results.append({
                "title": title,
                "company": None,
                "location": "Remote",
                "link": link,
                "source": "Wellfound",
                "snippet": title
            })
    except Exception as e:
        print("Wellfound scrape error:", e)
    return results


def scrape_generic_site_search(domain, kw):
    """
    Generic site: search via DuckDuckGo HTML query for a domain and keyword.
    (Using simple query URL.) Best-effort; may return search engine HTML.
    """
    results = []
    query = f"site:{domain} {kw} remote india"
    url = "https://duckduckgo.com/html"
    try:
        r = requests.get(url, params={"q": query}, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        time.sleep(SLEEP_BETWEEN_REQUESTS)
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.select("a.result__a")
        for a in links[:15]:
            link = a.get("href")
            title = normalize_text(a.get_text())
            snippet = ""
            parent = a.find_parent("div")
            if parent:
                snippet = normalize_text(parent.get_text())
            results.append({
                "title": title,
                "company": domain,
                "location": snippet,
                "link": link,
                "source": f"Search:{domain}",
                "snippet": snippet
            })
    except Exception as e:
        print("Generic search error for", domain, e)
    return results


# ---------- Orchestration ----------
def collect_jobs():
    jobs = []

    # 1) Search keywords on Indeed
    for kw in KEYWORDS:
        jobs.extend(scrape_indeed(kw))

    # 2) Wellfound (AngelList)
    for kw in KEYWORDS:
        jobs.extend(scrape_wellfound(kw))

    # 3) Generic site searches for major companies (best-effort)
    big_tech_domains = [
        "careers.google.com", "careers.amazon.com", "jobs.lever.co",
        "jobs.github.com", "netflixjobs.com", "careers.microsoft.com"
    ]
    for domain in big_tech_domains:
        for kw in KEYWORDS[:4]:
            jobs.extend(scrape_generic_site_search(domain, kw))

    # deduplicate by link
    uniq = {}
    for j in jobs:
        link = j.get("link") or j.get("title") or ""
        if not link:
            continue
        key = link
        if key not in uniq:
            uniq[key] = j

    results = list(uniq.values())
    print(f"Collected {len(results)} raw results")
    return results


def filter_jobs(raw_jobs):
    filtered = []
    for j in raw_jobs:
        # unify text to search for experience/location etc
        combined_text = " ".join([
            j.get("title") or "",
            j.get("company") or "",
            j.get("location") or "",
            j.get("snippet") or "",
        ])
        combined_text = normalize_text(combined_text)
        if not text_contains_keywords(combined_text):
            continue
        # parse experience phrases
        min_y, max_y = parse_experience_text(combined_text)
        if not experience_matches(min_y, max_y):
            continue
        if not location_matches(j.get("location") or j.get("snippet")):
            continue
        filtered.append(j)
    print(f"Filtered down to {len(filtered)} matching jobs")
    return filtered


def build_email_html(jobs):
    if not jobs:
        return "<p>No matching jobs found today.</p>"
    rows = []
    for idx, j in enumerate(jobs, 1):
        title = html.escape(j.get("title") or "—")
        company = html.escape(j.get("company") or j.get("source") or "—")
        location = html.escape(j.get("location") or "—")
        link = j.get("link") or "#"
        snippet = html.escape(j.get("snippet") or "")
        rows.append(
            f"<tr>"
            f"<td style='padding:6px;border:1px solid #ddd'>{idx}</td>"
            f"<td style='padding:6px;border:1px solid #ddd'><a href='{link}'>{title}</a></td>"
            f"<td style='padding:6px;border:1px solid #ddd'>{company}</td>"
            f"<td style='padding:6px;border:1px solid #ddd'>{location}</td>"
            f"<td style='padding:6px;border:1px solid #ddd'>{snippet}</td>"
            f"</tr>"
        )
    table = (
        "<table style='border-collapse:collapse;width:100%;font-family:Arial, sans-serif'>"
        "<thead><tr>"
        "<th style='padding:8px;border:1px solid #ddd'>#</th>"
        "<th style='padding:8px;border:1px solid #ddd'>Title</th>"
        "<th style='padding:8px;border:1px solid #ddd'>Company</th>"
        "<th style='padding:8px;border:1px solid #ddd'>Location</th>"
        "<th style='padding:8px;border:1px solid #ddd'>Snippet</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )
    head = f"<p>Found {len(jobs)} matching jobs on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.</p>"
    return head + table


def send_email(subject, html_body):
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_USER and GMAIL_APP_PASSWORD environment variables must be set.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SENDER_NAME} <{GMAIL_USER}>"
    msg["To"] = EMAIL_TO

    part1 = MIMEText("Open the HTML version of this message to see job listings.", "plain")
    part2 = MIMEText(html_body, "html")
    msg.attach(part1)
    msg.attach(part2)

    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
    server.ehlo()
    server.starttls()
    server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    server.sendmail(GMAIL_USER, [EMAIL_TO], msg.as_string())
    server.quit()
    print("Email sent to", EMAIL_TO)


def main():
    print("Starting job collection...")
    raw = collect_jobs()
    matches = filter_jobs(raw)
    html_body = build_email_html(matches)
    subject = f"🔎 Daily Cloud & DevOps Jobs – {datetime.utcnow().strftime('%Y-%m-%d')}"
    try:
        send_email(subject, html_body)
    except Exception as e:
        print("Failed to send email:", e)
        # still write results to local file for debugging
        with open("jobs_output.html", "w", encoding="utf-8") as f:
            f.write(html_body)


if __name__ == "__main__":
    main()
