"""The alphabetic codes of ISO 4217 that name a currency in force, written out so that a build reads no file or network.

A currency in the policy, the price list, the invoice or a silver record is one of these three upper-case letters.
Funds, precious metals and test codes (XAU, XDR, XXX and the like) are not currencies of a bill and are left out.
A code is added by editing this list.
"""

CODES = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD
HKD HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD
MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG
QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD
TZS UAH UGX USD UYU UZS VED VES VND VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG
""".split())  # noqa: SIM905 - kept as the whitespace-separated table it is copied and reviewed from


def is_iso_4217(code):
    """True when ``code`` is exactly one of the listed codes (no trimming, no case folding)."""
    return isinstance(code, str) and code in CODES
