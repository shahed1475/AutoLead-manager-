"""
enrichment/technology.py — which technology a business's website runs on, read
from the page's own HTML. Deterministic signatures only; every hit keeps the
snippet that matched, so the report can show *why* it says so.

(Separate from intelligence/techstack.py, which serves the opt-in sales
intelligence pipeline and stays as it is.)
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# (name, category, pattern). Keep patterns specific: a vendor's asset host,
# script name or init call — never a plain word that prose could contain.
_SIGNATURES: List[Tuple[str, str, str]] = [
    # Website platform / builder
    ("WordPress", "Website platform", r"wp-content/|wp-includes/|content=[\"']WordPress"),
    ("Wix", "Website platform", r"static\.wixstatic\.com|wix-bolt|_wixCssImports"),
    ("Squarespace", "Website platform", r"static1\.squarespace\.com|squarespace-cdn\.com"),
    ("Webflow", "Website platform", r"assets\.website-files\.com|data-wf-site"),
    ("Joomla", "Website platform", r"/media/jui/|content=[\"']Joomla"),
    ("Drupal", "Website platform", r"/sites/default/files/|Drupal\.settings|content=[\"']Drupal"),
    ("GoDaddy Website Builder", "Website platform", r"img1\.wsimg\.com|godaddy-builder"),
    ("Duda", "Website platform", r"dudaone|multiscreensite\.com"),
    ("Elementor", "Page builder", r"plugins/elementor/|elementor-frontend"),
    ("Divi", "Page builder", r"themes/Divi/|et_pb_"),
    ("WPBakery", "Page builder", r"js_composer|vc_row"),
    # E-commerce
    ("Shopify", "Online store", r"cdn\.shopify\.com|Shopify\.theme"),
    ("WooCommerce", "Online store", r"plugins/woocommerce/|woocommerce-"),
    ("Magento", "Online store", r"Magento_|mage/cookies"),
    ("BigCommerce", "Online store", r"cdn\d*\.bigcommerce\.com"),
    # Analytics & ads
    ("Google Analytics", "Analytics & ads", r"googletagmanager\.com/gtag/js|google-analytics\.com/(?:analytics|ga)\.js|gtag\(\s*['\"]config"),
    ("Google Tag Manager", "Analytics & ads", r"googletagmanager\.com/gtm\.js|GTM-[A-Z0-9]{4,}"),
    ("Google Ads", "Analytics & ads", r"googleadservices\.com|AW-\d{6,}"),
    ("Meta Pixel", "Analytics & ads", r"connect\.facebook\.net/[^\"']*fbevents\.js|fbq\("),
    ("TikTok Pixel", "Analytics & ads", r"analytics\.tiktok\.com"),
    ("LinkedIn Insight", "Analytics & ads", r"snap\.licdn\.com/li\.lms-analytics"),
    ("Hotjar", "Analytics & ads", r"static\.hotjar\.com|hjSiteSettings"),
    ("Microsoft Clarity", "Analytics & ads", r"clarity\.ms/tag"),
    # Chat & messaging
    ("WhatsApp chat widget", "Chat & messaging", r"plugins/(?:wp-whatsapp|click-to-chat|creame-whatsapp-me)|njt-whatsapp|joinchat"),
    ("Tawk.to", "Chat & messaging", r"embed\.tawk\.to"),
    ("Intercom", "Chat & messaging", r"widget\.intercom\.io|intercomSettings"),
    ("Crisp", "Chat & messaging", r"client\.crisp\.chat"),
    ("Tidio", "Chat & messaging", r"code\.tidio\.co"),
    ("LiveChat", "Chat & messaging", r"cdn\.livechatinc\.com"),
    ("Zendesk Chat", "Chat & messaging", r"static\.zdassets\.com|zopim"),
    ("HubSpot", "Chat & messaging", r"js\.hs-scripts\.com|js\.hsforms\.net"),
    # Booking & scheduling
    ("Calendly", "Booking", r"calendly\.com/"),
    ("Setmore", "Booking", r"setmore\.com"),
    ("SimplyBook.me", "Booking", r"simplybook\.(?:me|it)"),
    ("Acuity Scheduling", "Booking", r"acuityscheduling\.com"),
    ("Zocdoc", "Booking", r"zocdoc\.com"),
    ("Fresha", "Booking", r"fresha\.com"),
    ("Booksy", "Booking", r"booksy\.com"),
    ("NexHealth", "Booking", r"nexhealth\.com"),
    ("LocalMed", "Booking", r"localmed\.com"),
    # Payments
    ("Stripe", "Payments", r"js\.stripe\.com"),
    ("PayPal", "Payments", r"paypal\.com/sdk|paypalobjects\.com"),
    # SEO & forms
    ("Yoast SEO", "SEO", r"yoast-schema-graph|Yoast SEO"),
    ("Rank Math", "SEO", r"rank-math|RankMath"),
    ("Contact Form 7", "Forms", r"plugins/contact-form-7/|wpcf7"),
    ("Gravity Forms", "Forms", r"plugins/gravityforms/|gform_wrapper"),
    ("Google reCAPTCHA", "Forms", r"google\.com/recaptcha|grecaptcha"),
    # Frameworks, hosting & delivery
    ("React", "Framework", r"data-reactroot|react-dom(?:\.production)?\.min\.js"),
    ("Next.js", "Framework", r"__NEXT_DATA__|/_next/static/"),
    ("Vue.js", "Framework", r"vue(?:\.runtime)?(?:\.global)?(?:\.prod)?\.js|data-v-[0-9a-f]{8}"),
    ("jQuery", "Framework", r"jquery(?:\.min)?\.js|jquery-\d"),
    ("Bootstrap", "Framework", r"bootstrap(?:\.min)?\.(?:css|js)"),
    ("Cloudflare", "Hosting & delivery", r"cdnjs\.cloudflare\.com|cdn-cgi/|cf-ray"),
    ("Google Fonts", "Hosting & delivery", r"fonts\.googleapis\.com"),
    ("Google Maps embed", "Hosting & delivery", r"google\.com/maps/embed|maps\.googleapis\.com"),
]
_COMPILED = [(n, c, re.compile(p, re.I)) for n, c, p in _SIGNATURES]
CATEGORY_ORDER = ["Website platform", "Page builder", "Online store", "Booking", "Chat & messaging",
                  "Analytics & ads", "Payments", "SEO", "Forms", "Framework", "Hosting & delivery"]


def detect(html: Optional[str]) -> List[Dict[str, str]]:
    """[{name, category, evidence}] in report order; [] for no HTML."""
    if not html:
        return []
    found: List[Dict[str, str]] = []
    for name, category, rx in _COMPILED:
        m = rx.search(html)
        if m:
            a, b = max(0, m.start() - 30), min(len(html), m.end() + 30)
            found.append({"name": name, "category": category,
                          "evidence": re.sub(r"\s+", " ", html[a:b]).strip()})
    found.sort(key=lambda t: CATEGORY_ORDER.index(t["category"]))
    return found
