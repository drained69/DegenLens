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
        "ftx",
        ("ftx", "sam bankman-fried", "bankman-fried", "sbf", "alameda research"),
        "FTX, the cryptocurrency exchange founded by Sam Bankman-Fried, collapsed in "
        "November 2022 after customer funds were secretly funneled to its affiliated "
        "hedge fund, Alameda Research. Prosecutors said Bankman-Fried stole roughly "
        "$8 billion from FTX customers. A federal jury convicted him on seven "
        "fraud-related felony counts on November 2, 2023, and he was sentenced on "
        "March 28, 2024 to 25 years in prison.",
        "DOJ: Samuel Bankman-Fried Sentenced to 25 Years",
        "https://www.justice.gov/usao-sdny/pr/samuel-bankman-fried-sentenced-25-years-his-orchestration-multiple-fraudulent",
    ),
    FraudCase(
        "mtgox",
        ("mt. gox", "mt gox", "mtgox", "mark karpeles", "karpeles"),
        "Mt. Gox, once the largest bitcoin exchange, collapsed in February 2014 after "
        "a long-running breach that resulted in the loss of roughly 850,000 BTC, "
        "valued at about $450 million at the time. Around 200,000 BTC were later "
        "recovered. The exchange filed for bankruptcy protection in Japan, and chief "
        "executive Mark Karpeles was convicted of falsifying financial records and "
        "given a suspended sentence, while being acquitted of embezzlement. "
        "Creditor repayments began in 2024, a decade after the collapse.",
        "Mt. Gox civil rehabilitation proceedings",
        "https://www.mtgox.com/",
    ),
    FraudCase(
        "terra",
        ("terra", "luna", "terrausd", "ust", "do kwon", "terraform labs"),
        "TerraUSD, an algorithmic stablecoin operated by Terraform Labs, lost its "
        "dollar peg in May 2022, and the collapse of UST and its paired token LUNA "
        "erased roughly $40 billion of market value within days. The SEC charged "
        "Terraform Labs and founder Do Kwon with orchestrating a multi-billion "
        "dollar securities fraud, and a federal jury found them liable for fraud in "
        "April 2024. Terraform agreed to a settlement of about $4.5 billion. Kwon "
        "was arrested in Montenegro in March 2023 and later extradited to the "
        "United States.",
        "SEC v. Terraform Labs and Do Hyeong Kwon",
        "https://www.sec.gov/litigation/litreleases/lr-25948",
    ),
    FraudCase(
        "bitconnect",
        ("bitconnect", "bcc lending program", "satish kumbhani", "glenn arcaro"),
        "BitConnect was a cryptocurrency lending platform run by Satish Kumbhani. "
        "Its Lending Program falsely claimed a proprietary volatility software "
        "trading bot generated high returns, while earlier investors were in fact "
        "paid with later investors' money. U.S. prosecutors charged Kumbhani with "
        "running an approximately $2.4 billion global Ponzi scheme; he was indicted "
        "in February 2022 and remains a fugitive. Promoter Glenn Arcaro pleaded "
        "guilty and was sentenced to 38 months in prison. The platform shut down in "
        "January 2018. The charge against Kumbhani is an allegation unless proven "
        "in court.",
        "DOJ: BitConnect Founder Indicted in Global $2.4 Billion Cryptocurrency Scheme",
        "https://www.justice.gov/opa/pr/bitconnect-founder-indicted-global-24-billion-cryptocurrency-scheme",
    ),
    FraudCase(
        "onecoin",
        ("onecoin", "cryptoqueen", "ruja ignatova", "sebastian greenwood"),
        "OneCoin, launched in 2014 by Ruja Ignatova and Karl Sebastian Greenwood, "
        "was promoted as a cryptocurrency but had no genuine public blockchain and "
        "was run through a private database. It took in roughly $4 billion from "
        "investors worldwide through a pyramid structure selling fraudulent "
        "investment packages. Greenwood pleaded guilty and was sentenced to 20 years "
        "in prison in September 2023. Ignatova disappeared in October 2017, was "
        "charged in absentia, and was added to the FBI Ten Most Wanted Fugitives "
        "list.",
        "DOJ: OneCoin Co-Founder Sentenced to 20 Years",
        "https://www.justice.gov/usao-sdny/pr/co-founder-multi-billion-dollar-cryptocurrency-pyramid-scheme-onecoin-sentenced-20",
    ),
    FraudCase(
        "celsius",
        ("celsius network", "celsius", "alex mashinsky", "mashinsky"),
        "Celsius Network, a crypto-lending platform founded in 2017, froze customer "
        "withdrawals, swaps and transfers on June 12, 2022 and filed for Chapter 11 "
        "bankruptcy in July 2022 with a balance-sheet hole of roughly $1.2 billion "
        "and about $4.7 billion owed to users. Founder and chief executive Alex "
        "Mashinsky pleaded guilty to commodities fraud and securities fraud, "
        "admitting he misled customers about the business and manipulated the CEL "
        "token, and was sentenced to 12 years in prison in May 2025.",
        "DOJ: Celsius Founder Alex Mashinsky Sentenced",
        "https://www.justice.gov/usao-sdny/pr/celsius-founder-and-former-ceo-alex-mashinsky-sentenced-12-years-prison",
    ),
    FraudCase(
        "wirecard",
        ("wirecard", "markus braun", "jan marsalek"),
        "Wirecard, a German payments company once listed on the DAX, collapsed in "
        "June 2020 after its auditor refused to sign off on accounts and the company "
        "admitted that roughly EUR 1.9 billion supposedly held in trustee accounts "
        "in the Philippines did not exist. It filed for insolvency later that month. "
        "Former chief executive Markus Braun was arrested and charged with fraud, "
        "breach of trust and accounting manipulation, and chief operating officer "
        "Jan Marsalek fled and remains a fugitive.",
        "BaFin: Wirecard",
        "https://www.bafin.de/EN/Aufsicht/Wirecard/Wirecard_node_en.html",
    ),
    FraudCase(
        "bitfinex",
        ("bitfinex hack", "ilya lichtenstein", "heather morgan", "razzlekhan"),
        "In August 2016 a hacker stole roughly 119,754 BTC from the Bitfinex "
        "exchange. In February 2022 U.S. authorities seized about 94,000 of those "
        "bitcoin, then valued at roughly $3.6 billion, in what was at the time the "
        "largest financial seizure in Justice Department history. Ilya Lichtenstein "
        "pleaded guilty to carrying out the hack and to money laundering conspiracy "
        "and was sentenced to five years in prison in November 2024; his wife "
        "Heather Morgan pleaded guilty to laundering the proceeds and received 18 "
        "months.",
        "DOJ: Individual Sentenced for 2016 Hack of Bitfinex",
        "https://www.justice.gov/opa/pr/individual-sentenced-2016-hack-virtual-currency-exchange",
    ),
    FraudCase(
        "plustoken",
        ("plustoken", "plus token"),
        "PlusToken was a cryptocurrency Ponzi scheme operating chiefly in China and "
        "South Korea that promised high returns on deposits and took in roughly $2 "
        "billion to $3 billion from around two million investors before collapsing "
        "in 2019. Chinese courts convicted more than a dozen participants, handing "
        "down prison terms of up to 11 years, and authorities seized large amounts "
        "of bitcoin and ether tied to the scheme.",
        "Chinese court judgments on the PlusToken scheme",
        "https://www.chainalysis.com/blog/plustoken-scam-bitcoin-price/",
    ),
    FraudCase(
        "ronin",
        ("ronin bridge", "ronin network", "axie infinity"),
        "In March 2022 attackers drained roughly $620 million in ether and USDC from "
        "the Ronin bridge used by the Axie Infinity game, by compromising five of "
        "the nine validator keys securing the bridge. The theft went unnoticed for "
        "about six days. The U.S. Treasury attributed the attack to the "
        "North Korea-linked Lazarus Group and sanctioned the receiving address.",
        "U.S. Treasury: Sanctioning of Lazarus Group address",
        "https://home.treasury.gov/policy-issues/financial-sanctions/recent-actions/20220414",
    ),
    FraudCase(
        "mango",
        ("mango markets", "avraham eisenberg", "avi eisenberg"),
        "In October 2022 Avraham Eisenberg manipulated the price of the MNGO "
        "perpetual swap on the Mango Markets decentralized exchange, inflating the "
        "value of his collateral and borrowing against it to drain roughly $110 "
        "million from the protocol. He was convicted of fraud and market "
        "manipulation in April 2024, though the trial court later overturned the "
        "core fraud counts.",
        "DOJ: Mango Markets manipulation prosecution",
        "https://www.justice.gov/usao-sdny/pr/mango-markets-manipulator-convicted",
    ),
    FraudCase(
        "thodex",
        ("thodex", "faruk fatih ozer", "faruk fatih özer"),
        "Thodex was a Turkish cryptocurrency exchange that halted trading in April "
        "2021 while founder Faruk Fatih Ozer fled the country, with investor losses "
        "widely reported at roughly $2 billion. Ozer was extradited from Albania and "
        "in September 2023 a Turkish court sentenced him and two co-defendants to "
        "11,196 years in prison for fraud, money laundering and organised crime.",
        "Turkish court judgment in the Thodex prosecution",
        "https://www.reuters.com/technology/turkish-crypto-boss-sentenced-11196-years-jail-2023-09-07/",
    ),
    FraudCase(
        "theranos",
        ("theranos", "elizabeth holmes", "ramesh balwani", "sunny balwani"),
        "Theranos founder Elizabeth Holmes falsely represented the capabilities and "
        "commercial performance of the company's blood-testing technology, raising "
        "more than $700 million from investors. A federal jury convicted her in "
        "January 2022 on four counts of defrauding investors, and she was sentenced "
        "to 135 months in prison. Former president Ramesh Balwani was convicted "
        "separately and sentenced to 155 months.",
        "DOJ: Elizabeth Holmes Sentenced to More Than 11 Years",
        "https://www.justice.gov/usao-ndca/pr/elizabeth-holmes-sentenced-more-11-years-defrauding-theranos-investors-hundreds",
    ),
    FraudCase(
        "madoff",
        ("bernie madoff", "bernard madoff", "madoff"),
        "Bernard Madoff operated the largest Ponzi scheme in history through his "
        "investment advisory business, fabricating decades of trading activity and "
        "paying withdrawals out of new investor money. Customer statements showed "
        "roughly $65 billion in fictitious balances, against about $17.5 billion in "
        "actual principal invested. He was arrested in December 2008, pleaded guilty "
        "to 11 federal felonies in March 2009, and was sentenced to 150 years in "
        "prison in June 2009. He died in prison in 2021.",
        "FBI: Bernard Madoff",
        "https://www.fbi.gov/history/famous-cases/bernard-madoff",
    ),
    FraudCase(
        "enron",
        ("enron", "jeffrey skilling", "kenneth lay", "andrew fastow"),
        "Enron executives used deceptive accounting and off-balance-sheet special "
        "purpose entities to hide debt and inflate reported performance. The company "
        "filed for bankruptcy in December 2001, then the largest corporate "
        "bankruptcy in U.S. history, wiping out around $74 billion in shareholder "
        "value and the retirement savings of thousands of employees. A federal jury "
        "convicted Jeffrey Skilling and Kenneth Lay of fraud and conspiracy in May "
        "2006; Lay died before sentencing and his conviction was vacated, while "
        "Skilling's sentence was later reduced to 14 years. The scandal also "
        "destroyed auditor Arthur Andersen and led to the Sarbanes-Oxley Act.",
        "FBI: Enron",
        "https://www.fbi.gov/history/famous-cases/enron",
    ),
    FraudCase(
        "quadrigacx",
        ("quadrigacx", "quadriga cx", "quadriga", "gerald cotten"),
        "QuadrigaCX was Canada's largest cryptocurrency exchange until it collapsed "
        "in early 2019 following the reported death of founder Gerald Cotten in "
        "December 2018, leaving roughly C$215 million owed to about 76,000 users. "
        "The Ontario Securities Commission concluded the exchange operated as a "
        "fraud: Cotten misappropriated client assets, traded against customers using "
        "fictitious accounts, and used new deposits to fund withdrawals. That "
        "finding was made in a regulator's investigative report after Cotten's "
        "death, not in a criminal conviction.",
        "Ontario Securities Commission: QuadrigaCX Report",
        "https://www.osc.ca/quadrigacxreport/",
    ),
    FraudCase(
        "silkroad",
        ("silk road", "ross ulbricht", "dread pirate roberts"),
        "Silk Road was a darknet marketplace operating from 2011 to 2013 that used "
        "bitcoin and Tor to facilitate anonymous sales, largely of illegal drugs, "
        "handling several hundred million dollars in transactions. Founder Ross "
        "Ulbricht, who operated as Dread Pirate Roberts, was convicted in February "
        "2015 on seven counts including narcotics conspiracy and money laundering "
        "and sentenced to life in prison without parole. He was pardoned in January "
        "2025.",
        "FBI: Silk Road takedown",
        "https://www.fbi.gov/news/stories/manhattan-us-attorney-announces-seizure-of-silk-road",
    ),
    FraudCase(
        "polynetwork",
        ("poly network",),
        "In August 2021 an attacker exploited a flaw in the Poly Network "
        "cross-chain protocol and moved roughly $610 million in assets across "
        "Ethereum, BNB Chain and Polygon, then the largest decentralized finance "
        "exploit recorded. The attacker returned substantially all of the funds "
        "within days and the protocol offered a bug bounty.",
        "Poly Network incident disclosure",
        "https://www.elliptic.co/blog/the-poly-network-hack-what-we-know",
    ),
    FraudCase(
        "wormhole",
        ("wormhole bridge", "wormhole hack"),
        "In February 2022 an attacker exploited a signature verification flaw in the "
        "Wormhole bridge between Solana and Ethereum to mint 120,000 wrapped ether "
        "without collateral, worth roughly $326 million at the time. Jump Crypto "
        "replaced the missing funds to keep the bridge solvent.",
        "Wormhole incident report",
        "https://wormholecrypto.medium.com/wormhole-incident-report-02-02-22-ad9b8f21eec6",
    ),
    FraudCase(
        "africrypt",
        ("africrypt", "raees cajee", "ameer cajee"),
        "Africrypt was a South African cryptocurrency investment platform that "
        "stopped operating in April 2021, with founders Raees and Ameer Cajee "
        "telling investors the company had been hacked before becoming "
        "uncontactable. Liquidators and investors alleged losses of up to roughly "
        "$3.6 billion in bitcoin, though later estimates put the recoverable and "
        "verifiable amount far lower. The allegations have not produced a criminal "
        "conviction.",
        "South African liquidation proceedings reporting",
        "https://www.bloomberg.com/news/articles/2021-06-23/s-african-brothers-vanish-along-with-3-6-billion-in-bitcoin",
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
