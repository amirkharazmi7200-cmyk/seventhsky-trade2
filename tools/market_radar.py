#!/usr/bin/env python3
"""Seventh Sky buyer-intent radar.

The radar only discovers and stores opportunities. It never sends outreach.
Every stored signal is explicitly marked as requiring Amir's approval.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any


API_BASE = os.environ.get(
    "RADAR_API_BASE",
    "https://app.7skytrade.com/api-proxy.php",
).rstrip("?")
DRY_RUN = os.environ.get("RADAR_DRY_RUN", "").lower() in {"1", "true", "yes"}
MAX_AGE_DAYS = int(os.environ.get("RADAR_MAX_AGE_DAYS", "180"))
MAX_RESULTS = int(os.environ.get("RADAR_MAX_RESULTS", "40"))
USER_AGENT = "Mozilla/5.0 (compatible; SeventhSkyMarketRadar/3.0; +https://7skytrade.com)"
NOW = datetime.now(timezone.utc)

PRODUCT_RE = re.compile(
    r"\b(date fruit|dates?|kurma|kabkab|mazafati|zahedi|zahidi|kimia|medjool|ajwa|sukari|piaro?m)\b",
    re.I,
)
HIGH_INTENT_RE = re.compile(
    r"\b(rfq|request for quotation|buy lead|buyer requirement|wanted|looking for supplier|seeking supplier|procurement)\b"
    r"|\b(mencari|butuh|dicari|permintaan penawaran|pembeli)\b",
    re.I,
)
BUYER_RE = re.compile(
    r"\b(buyer|buying|importer|import|distributor|wholesaler|procurement|sourcing|purchase)\b"
    r"|\b(importir|distributor|grosir|pembeli|mencari|pengadaan)\b",
    re.I,
)
NOISE_RE = re.compile(
    r"\b(recipe|dating|calendar|festival|tourism|job|jobs|health benefit|school date|event date)\b",
    re.I,
)


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(html.unescape(data).split())
        if cleaned:
            self.parts.append(cleaned)


@dataclass
class Signal:
    title: str
    url: str
    market_source: str
    country: str = "Indonesia"
    product: str = "Dates / Kurma"
    notes: str = ""
    published_at: str = ""
    contact: str = ""
    source_type: str = "web_discovery"
    access_mode: str = ""
    quantity: str = ""
    destination: str = ""
    payment_terms: str = ""
    shipping_terms: str = ""
    query: str = ""
    age_days: int | None = None
    intent_score: int = 0
    score: str = "C"
    priority_reason: str = ""
    key: str = field(init=False)
    legacy_key: str = field(init=False)

    def __post_init__(self) -> None:
        legacy_stable = "|".join(
            [
                self.market_source.strip(),
                normalize_url(self.url),
                clean(self.title),
            ]
        )
        self.legacy_key = hashlib.sha256(legacy_stable.encode("utf-8")).hexdigest()[:20]
        stable = "|".join(
            [
                self.market_source.lower().strip(),
                normalize_url(self.url),
                clean(self.title).lower(),
                clean(self.published_at).lower(),
                clean(self.contact).lower(),
            ]
        )
        self.key = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:20]
        self.age_days = source_age_days(self.published_at)
        self.intent_score, self.score, self.priority_reason = score_signal(self)

    @property
    def message_key(self) -> str:
        return f"market-radar-{self.key}"

    @property
    def lead_id(self) -> str:
        return f"radar-{self.key}"

    @property
    def legacy_message_key(self) -> str:
        return f"market-radar-{self.legacy_key}"


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def normalize_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        filtered = [
            (key, val)
            for key, val in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"gclid", "fbclid", "srsltid"}
        ]
        path = re.sub(r"/+", "/", parsed.path or "/")
        return urllib.parse.urlunsplit(
            (parsed.scheme.lower(), parsed.netloc.lower(), path, urllib.parse.urlencode(filtered), "")
        )
    except Exception:
        return value.strip()


def source_date(value: str) -> datetime | None:
    text = clean(value)
    if not text:
        return None
    for fmt in ("%b-%d-%y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        except ValueError:
            pass
    try:
        parsed = parsedate_to_datetime(text)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def source_age_days(value: str) -> int | None:
    parsed = source_date(value)
    if not parsed:
        return None
    return max(0, int((NOW - parsed.astimezone(timezone.utc)).total_seconds() // 86400))


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9,id;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=22) as response:
        return response.read()


def api_url(resource: str, **query: Any) -> str:
    params = {"resource": resource, **{key: value for key, value in query.items() if value != ""}}
    return f"{API_BASE}?{urllib.parse.urlencode(params)}"


def read_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    if data.get("ok") is False:
        raise RuntimeError(data.get("error", "api_error"))
    return data


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    if data.get("ok") is False:
        raise RuntimeError(data.get("error", "api_error"))
    return data


def field_value(lines: list[str], labels: tuple[str, ...]) -> str:
    for index, line in enumerate(lines):
        lower = line.lower().strip()
        for label in labels:
            needle = label.lower()
            if lower.startswith(needle):
                tail = line[len(label) :].lstrip(" :–-").strip()
                if tail:
                    return tail
                for candidate in lines[index + 1 : index + 4]:
                    candidate = clean(candidate).strip(" :–-")
                    if candidate and not any(candidate.lower().startswith(other.lower()) for other in labels):
                        return candidate
    return ""


def score_signal(signal: Signal) -> tuple[int, str, str]:
    score = 0
    reasons: list[str] = []
    text = " ".join([signal.title, signal.notes, signal.product])

    if signal.source_type == "direct_buy_lead":
        score += 42
        reasons.append("direct RFQ")
    elif HIGH_INTENT_RE.search(text):
        score += 28
        reasons.append("explicit buyer intent")
    elif BUYER_RE.search(text):
        score += 18
        reasons.append("buyer/importer signal")

    if signal.country.lower() == "indonesia" or "indonesia" in text.lower():
        score += 20
        reasons.append("Indonesia target")

    commercial_fields = sum(
        bool(value)
        for value in (
            signal.quantity,
            signal.destination,
            signal.payment_terms,
            signal.shipping_terms,
        )
    )
    if commercial_fields:
        score += min(16, commercial_fields * 4)
        reasons.append(f"{commercial_fields} commercial fields")

    if signal.contact:
        score += 5
        reasons.append("named contact")

    if signal.age_days is None:
        score += 3
        reasons.append("source date unknown")
    elif signal.age_days <= 7:
        score += 22
        reasons.append("published ≤7d")
    elif signal.age_days <= 30:
        score += 16
        reasons.append("published ≤30d")
    elif signal.age_days <= 90:
        score += 8
        reasons.append("published ≤90d")
    elif signal.age_days > MAX_AGE_DAYS:
        score -= 25
        reasons.append("outside freshness window")

    if HIGH_INTENT_RE.search(text):
        score += 8

    score = max(0, min(100, score))
    grade = "A" if score >= 75 else "B" if score >= 50 else "C"
    return score, grade, ", ".join(reasons)


def discover_go4worldbusiness() -> list[Signal]:
    url = "https://www.go4worldbusiness.com/buyers/indonesia/dates.html"
    parser = TextParser()
    parser.feed(fetch_bytes(url).decode("utf-8", "replace"))
    lines = parser.parts
    date_re = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{2}-\d{2}$", re.I)
    indexes = [index for index, line in enumerate(lines) if date_re.match(line)]
    signals: list[Signal] = []

    for position, index in enumerate(indexes):
        end = indexes[position + 1] if position + 1 < len(indexes) else min(len(lines), index + 120)
        block = lines[index : min(end, index + 120)]
        block_text = " | ".join(block)
        if not PRODUCT_RE.search(block_text):
            continue
        if "Buyer From Indonesia" not in block_text and "Indonesia" not in block_text:
            continue
        published = block[0]
        age = source_age_days(published)
        if age is not None and age > MAX_AGE_DAYS:
            continue

        title = next((line for line in block if re.match(r"^Wanted\s*:", line, re.I)), "")
        if not title:
            title = next(
                (
                    line
                    for line in block[1:24]
                    if PRODUCT_RE.search(line)
                    and line not in {"Inquire Now", "Add to Favorites", "Product Description"}
                    and not line.startswith(("Quantity", "Payment", "Shipping", "Destination"))
                ),
                "Indonesia dates buyer requirement",
            )

        quantity = field_value(block, ("Quantity Required", "Quantity"))
        shipping = field_value(block, ("Shipping Terms",))
        payment = field_value(block, ("Payment Terms",))
        destination = field_value(block, ("Destination Port", "Destination"))
        contact = field_value(block, ("Contact",))
        product = field_value(block, ("Product Name", "Type")) or "Dates / Kurma"
        requirement = field_value(block, ("Specifications", "Product Description"))
        notes_parts = [
            f"Buyer From Indonesia",
            f"Product: {product}" if product else "",
            f"Quantity: {quantity}" if quantity else "",
            f"Destination: {destination}" if destination else "",
            f"Shipping: {shipping}" if shipping else "",
            f"Payment: {payment}" if payment else "",
            f"Requirement: {requirement}" if requirement else "",
        ]
        notes = " | ".join(part for part in notes_parts if part)
        if len(notes) < 80:
            notes = clean(block_text[:1000])

        signals.append(
            Signal(
                title=title[:220],
                url=url,
                market_source="Go4WorldBusiness Buy Leads",
                product=product[:180],
                notes=notes[:1300],
                published_at=published,
                contact=contact[:180],
                source_type="direct_buy_lead",
                quantity=quantity[:180],
                destination=destination[:180],
                payment_terms=payment[:180],
                shipping_terms=shipping[:180],
                query="direct Indonesia dates buyer page",
            )
        )
    return signals


SEARCH_QUERIES: tuple[tuple[str, str], ...] = (
    ("TradeWheel", "site:tradewheel.com/buyers dates Indonesia buyer requirement"),
    ("TradeKey", "site:tradekey.com Indonesia dates buy offer RFQ"),
    ("ExportHub", "site:exporthub.com Indonesia dates buyer importer"),
    ("Go4WorldBusiness", "site:go4worldbusiness.com Indonesia dates buyer RFQ"),
    ("Indotrading", 'site:indotrading.com "permintaan penawaran" kurma'),
    ("Indonetwork", 'site:indonetwork.co.id "mencari" supplier kurma'),
    ("B2B Web", '"mencari supplier kurma" Indonesia'),
    ("B2B Web", '"butuh supplier kurma" Indonesia'),
    ("B2B Web", '"request for quotation" dates Indonesia'),
    ("B2B Web", '"looking for supplier" dates Indonesia'),
    ("B2B Web", '"dates buyer from Indonesia"'),
)


def discover_search_query(source: str, query: str) -> tuple[list[Signal], str | None]:
    signals: list[Signal] = []
    try:
        url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "format": "rss"})
        root = ET.fromstring(fetch_bytes(url))
        for item in root.findall(".//item")[:15]:
            title = clean(item.findtext("title"))
            link = clean(item.findtext("link"))
            description = clean(item.findtext("description"))
            published = clean(item.findtext("pubDate"))
            text = " ".join([title, description, link])
            if not title or not link or NOISE_RE.search(text):
                continue
            if not PRODUCT_RE.search(text) or not BUYER_RE.search(text):
                continue
            if "indonesia" not in text.lower() and ".id/" not in link.lower():
                continue
            age = source_age_days(published)
            if age is not None and age > MAX_AGE_DAYS:
                continue
            source_type = "direct_buy_lead" if HIGH_INTENT_RE.search(text) else "buyer_directory"
            signal = Signal(
                title=title[:220],
                url=link,
                market_source=source,
                notes=description[:1300],
                published_at=published,
                source_type=source_type,
                query=query,
            )
            if signal.score in {"A", "B"}:
                signals.append(signal)
        return signals, None
    except Exception as exc:  # Keep other sources alive.
        return [], f"{source}: {type(exc).__name__}: {str(exc)[:180]}"


def discover_search_feeds() -> tuple[list[Signal], list[str]]:
    signals: list[Signal] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(8, len(SEARCH_QUERIES))) as executor:
        futures = {
            executor.submit(discover_search_query, source, query): (source, query)
            for source, query in SEARCH_QUERIES
        }
        for future in as_completed(futures):
            rows, error = future.result()
            signals.extend(rows)
            if error:
                errors.append(error)
    return signals, errors


def encode_text(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")


def storage_event(signal: Signal) -> dict[str, Any]:
    access_mode = signal.access_mode
    if not access_mode:
        platform_hint = " ".join([signal.market_source, signal.url]).lower()
        access_mode = (
            "login_required"
            if any(
                name in platform_hint
                for name in ("go4worldbusiness", "tradewheel", "tradekey", "exporthub")
            )
            else "public_source"
        )
    return {
        "messageKey": signal.message_key,
        "source": "market_radar",
        "radar": True,
        "id": signal.lead_id,
        "company": signal.title,
        "country": signal.country,
        "product": signal.product,
        "score": signal.score,
        "intentScore": signal.intent_score,
        "priorityReason": signal.priority_reason,
        "marketSource": signal.market_source,
        "publishedAt": signal.published_at or NOW.isoformat(),
        "ageDays": signal.age_days,
        "firstSeenAt": NOW.isoformat(),
        "contact": signal.contact,
        "sourceLinkB64": encode_text(normalize_url(signal.url)),
        "notesB64": encode_text(signal.notes),
        "sourceType": signal.source_type,
        "accessMode": access_mode,
        "quantity": signal.quantity,
        "destination": signal.destination,
        "paymentTerms": signal.payment_terms,
        "shippingTerms": signal.shipping_terms,
        "query": signal.query,
        "outreachStatus": "awaiting_approval",
        "outreachMode": "approval_then_manual_send",
        "requiresApproval": True,
        "autoSend": False,
    }


def existing_message_keys() -> set[str]:
    data = read_json(api_url("inbox", limit=300, source="market_radar"))
    return {
        str(event.get("messageKey"))
        for event in data.get("events", [])
        if isinstance(event, dict) and event.get("messageKey")
    }


def main() -> int:
    signals: list[Signal] = []
    source_errors: list[str] = []
    source_successes = 0

    try:
        signals.extend(discover_go4worldbusiness())
        source_successes += 1
    except Exception as exc:
        source_errors.append(f"Go4WorldBusiness: {type(exc).__name__}: {str(exc)[:180]}")

    search_signals, search_errors = discover_search_feeds()
    signals.extend(search_signals)
    source_errors.extend(search_errors)
    source_successes += len(SEARCH_QUERIES) - len(search_errors)

    unique: dict[str, Signal] = {}
    for signal in signals:
        current = unique.get(signal.message_key)
        if current is None or signal.intent_score > current.intent_score:
            unique[signal.message_key] = signal
    ranked = sorted(
        unique.values(),
        key=lambda item: (item.intent_score, -(item.age_days if item.age_days is not None else 9999)),
        reverse=True,
    )[:MAX_RESULTS]

    if DRY_RUN:
        print(
            json.dumps(
                {
                    "ok": source_successes > 0,
                    "dryRun": True,
                    "found": len(signals),
                    "unique": len(ranked),
                    "signals": [storage_event(item) for item in ranked],
                    "sourceErrors": source_errors[:12],
                },
                ensure_ascii=False,
            )
        )
        return 0 if source_successes > 0 else 1

    try:
        existing = existing_message_keys()
    except Exception as exc:
        print(json.dumps({"ok": False, "stage": "readback_before", "error": str(exc)}, ensure_ascii=False))
        return 1

    fresh = [
        signal
        for signal in ranked
        if signal.message_key not in existing and signal.legacy_message_key not in existing
    ]
    ingested: list[str] = []
    ingest_errors: list[str] = []
    for signal in fresh:
        try:
            post_json(api_url("inbox"), storage_event(signal))
            ingested.append(signal.message_key)
        except Exception as exc:
            ingest_errors.append(f"{signal.title[:80]}: {type(exc).__name__}: {str(exc)[:220]}")

    verified: list[str] = []
    if ingested:
        try:
            present = existing_message_keys()
            verified = [key for key in ingested if key in present]
            missing = sorted(set(ingested) - set(verified))
            if missing:
                ingest_errors.append("readback_missing:" + ",".join(missing))
        except Exception as exc:
            ingest_errors.append(f"readback_after: {type(exc).__name__}: {str(exc)[:220]}")

    result = {
        "ok": source_successes > 0 and (not ingested or len(verified) == len(ingested)),
        "found": len(signals),
        "unique": len(ranked),
        "alreadyStored": len(ranked) - len(fresh),
        "new": len(fresh),
        "ingested": len(ingested),
        "verified": len(verified),
        "scoreA": sum(item.score == "A" for item in fresh),
        "scoreB": sum(item.score == "B" for item in fresh),
        "autoSend": False,
        "sourceErrors": source_errors[:12],
        "errors": ingest_errors[:12],
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] and not ingest_errors else 1


if __name__ == "__main__":
    sys.exit(main())
