"""Bounded, source-backed knowledge for documented fraud cases.

The FRAUD_DETECTION intent covers named entities and incidents as well as
wallets. Generic search is too noisy for a scored factual answer, so only an
explicit alias for a reviewed case resolves here. Unknown names return None and
the endpoint abstains rather than fabricating an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FraudCase:
    key: str
    aliases: tuple[str, ...]
    answer: str
    source_title: str
    source_url: str


CASES = (
    FraudCase(
        "bitconnect",
        ("bitconnect", "bcc lending program"),
        "BitConnect operated a cryptocurrency lending program that promised returns from a purported trading bot while paying earlier investors with later investors' money. U.S. prosecutors charged founder Satish Kumbhani with running an approximately $2.4 billion global Ponzi scheme; promoter Glenn Arcaro separately pleaded guilty. The charge against Kumbhani is an allegation unless proven in court.",
        "DOJ: BitConnect Founder Indicted in Global $2.4 Billion Cryptocurrency Scheme",
        "https://www.justice.gov/opa/pr/bitconnect-founder-indicted-global-24-billion-cryptocurrency-scheme",
    ),
    FraudCase(
        "onecoin",
        ("onecoin", "cryptoqueen", "ruja ignatova"),
        "OneCoin was a multibillion-dollar cryptocurrency pyramid scheme that sold fraudulent investment packages for a purported coin that was not mined on a public blockchain. Co-founder Karl Sebastian Greenwood pleaded guilty and was sentenced to 20 years in prison; founder Ruja Ignatova was charged and remains a fugitive.",
        "DOJ: OneCoin Co-Founder Sentenced to 20 Years",
        "https://www.justice.gov/usao-sdny/pr/co-founder-multi-billion-dollar-cryptocurrency-pyramid-scheme-onecoin-sentenced-20",
    ),
    FraudCase(
        "ftx",
        ("ftx", "sam bankman-fried", "bankman-fried"),
        "FTX collapsed in November 2022 after customer funds were misappropriated and transferred to affiliated trading firm Alameda Research. A federal jury convicted founder Sam Bankman-Fried of fraud, conspiracy, and money laundering, and he was sentenced to 25 years in prison in March 2024.",
        "DOJ: Samuel Bankman-Fried Sentenced to 25 Years",
        "https://www.justice.gov/usao-sdny/pr/samuel-bankman-fried-sentenced-25-years-his-orchestration-multiple-fraudulent",
    ),
    FraudCase(
        "celsius",
        ("celsius network", "alex mashinsky", "mashinsky"),
        "Celsius Network founder Alex Mashinsky pleaded guilty to commodities fraud and securities fraud after admitting that he misled customers about Celsius's business and manipulated the CEL token. He was sentenced to 12 years in prison in May 2025.",
        "DOJ: Celsius Founder Alex Mashinsky Sentenced",
        "https://www.justice.gov/usao-sdny/pr/celsius-founder-and-former-ceo-alex-mashinsky-sentenced-12-years-prison",
    ),
    FraudCase(
        "theranos",
        ("theranos", "elizabeth holmes"),
        "Theranos founder Elizabeth Holmes falsely represented the capabilities and commercial performance of the company's blood-testing technology to investors. A federal jury convicted her of investor fraud, and she was sentenced to 135 months in prison.",
        "DOJ: Elizabeth Holmes Sentenced to More Than 11 Years",
        "https://www.justice.gov/usao-ndca/pr/elizabeth-holmes-sentenced-more-11-years-defrauding-theranos-investors-hundreds",
    ),
    FraudCase(
        "madoff",
        ("bernie madoff", "bernard madoff", "madoff"),
        "Bernard Madoff operated a decades-long Ponzi scheme through his investment advisory business, fabricating trading activity and using new investor money to fund withdrawals. He pleaded guilty to 11 federal felonies and was sentenced to 150 years in prison.",
        "FBI: Bernard Madoff",
        "https://www.fbi.gov/history/famous-cases/bernard-madoff",
    ),
    FraudCase(
        "enron",
        ("enron", "jeffrey skilling", "kenneth lay"),
        "Enron executives used deceptive accounting and off-balance-sheet entities to hide debt and inflate reported performance before the company collapsed in 2001. A federal jury convicted senior executives including Jeffrey Skilling of fraud and conspiracy; Kenneth Lay was convicted but died before sentencing, and his conviction was later vacated under then-applicable law.",
        "FBI: Enron",
        "https://www.fbi.gov/history/famous-cases/enron",
    ),
    FraudCase(
        "quadrigacx",
        ("quadrigacx", "quadriga cx", "gerald cotten"),
        "The Ontario Securities Commission concluded that QuadrigaCX operated as a fraud and that founder Gerald Cotten misappropriated client assets, traded against customers using fictitious accounts, and used new deposits to fund withdrawals. The finding was made in a regulator's investigative report after Cotten's death, not in a criminal conviction.",
        "Ontario Securities Commission: QuadrigaCX Report",
        "https://www.osc.ca/quadrigacxreport/",
    ),
)


def find_case(query: str) -> FraudCase | None:
    """Return a reviewed case only when an explicit alias is present."""
    text = " ".join(query.lower().split())
    for case in CASES:
        for alias in case.aliases:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text):
                return case
    return None
