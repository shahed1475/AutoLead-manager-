"""
service_knowledge_base.py — static PopupGenix service catalog.

Pure data, no logic, no I/O. solution_matcher.py scores against this list;
nothing here decides anything on its own. Deliberately static (not derived
from company_dna.txt, which is outreach-messaging content for a specific
current positioning) — this is the general, industry-agnostic service
catalog Phase 2 asks for, spanning every vertical AutoLead's leads come
from (dental, restaurant, real estate, e-commerce, agency, ...).

`recommend_when` / `do_not_recommend_when` are lowercase keyword/condition
strings matched (substring) against a candidate's combined signal text
(opportunity name + business_ease + industry + tech_stack + pain point
titles) by solution_matcher.match_solution(). Keep them specific — vague
keywords cause over-matching and violate "never invent a reason to sell it."
"""
from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class ServiceDefinition:
    name: str
    problems_solved: List[str]
    target_businesses: List[str]
    use_cases: List[str]
    benefits: List[str]
    recommend_when: List[str]
    do_not_recommend_when: List[str] = field(default_factory=list)


SERVICE_KNOWLEDGE_BASE: List[ServiceDefinition] = [

    ServiceDefinition(
        name="WhatsApp Automation",
        problems_solved=[
            "Manual, one-at-a-time customer messaging",
            "Missed customer replies outside business hours",
            "No automated appointment/order reminders",
        ],
        target_businesses=["dental", "clinic", "restaurant", "salon", "gym", "retail", "local service business"],
        use_cases=[
            "Automated appointment reminders and confirmations",
            "Order status updates",
            "Answering common customer questions via WhatsApp",
        ],
        benefits=[
            "Reduces manual staff messaging workload",
            "Reaches customers on a channel they already use",
            "Cuts no-shows via automated reminders",
        ],
        recommend_when=[
            "manual customer communication", "no visible online appointment", "no visible online booking",
            "no easy way for customers to reach the business", "appointment reminder", "manual scheduling",
            "manual ordering", "customer communication",
        ],
        do_not_recommend_when=["ecommerce platform", "e-commerce platform", "already has crm"],
    ),

    ServiceDefinition(
        name="AI Chatbots",
        problems_solved=[
            "Repetitive customer questions consuming staff time",
            "No 24/7 response to website visitors",
        ],
        target_businesses=["any business with a website and repetitive customer questions"],
        use_cases=[
            "Website FAQ answering",
            "Lead capture on-site",
            "After-hours visitor engagement",
        ],
        benefits=[
            "24/7 coverage without added headcount",
            "Consistent answers to common questions",
        ],
        recommend_when=[
            "no active social media presence", "repetitive question", "no easy way for customers to reach",
            "website visitor", "lost website visitor", "no contact form",
        ],
        do_not_recommend_when=["no website", "website unreachable"],
    ),

    ServiceDefinition(
        name="RAG",
        problems_solved=[
            "Staff/customers can't quickly find answers buried in existing documents or content",
        ],
        target_businesses=["businesses with large document sets, catalogs, or knowledge bases"],
        use_cases=[
            "Answering questions grounded in a business's own documents/policies/catalog",
            "Internal knowledge search for staff",
        ],
        benefits=["Accurate, source-grounded answers instead of generic responses"],
        recommend_when=["large catalog", "knowledge base", "documentation", "policy lookup"],
        do_not_recommend_when=["small business", "no documented content"],
    ),

    ServiceDefinition(
        name="Agentic AI",
        problems_solved=[
            "Multi-step manual workflows that require judgment across several tools/steps",
        ],
        target_businesses=["businesses with complex, multi-step operational workflows"],
        use_cases=[
            "End-to-end lead qualification across multiple data sources",
            "Multi-step customer-service resolution",
        ],
        benefits=["Handles multi-step tasks without a human in every step"],
        recommend_when=["multi-step", "complex workflow", "lead qualification", "manual lead intake"],
        do_not_recommend_when=["single simple task", "one-off request"],
    ),

    ServiceDefinition(
        name="AI Automation",
        problems_solved=[
            "Repetitive manual operational tasks (data entry, follow-ups, notifications)",
        ],
        target_businesses=["any business with repetitive back-office processes"],
        use_cases=[
            "Automated follow-up sequences",
            "Automated data entry / status updates",
        ],
        benefits=["Frees staff time from repetitive tasks", "Reduces human error in routine processes"],
        recommend_when=["staff workload", "repetitive", "manual follow-up", "manual process", "administrative burden"],
        do_not_recommend_when=[],
    ),

    ServiceDefinition(
        name="Generative AI",
        problems_solved=[
            "Slow, manual content creation (descriptions, replies, summaries)",
        ],
        target_businesses=["businesses that regularly produce written content or replies"],
        use_cases=[
            "Auto-drafting customer replies",
            "Auto-generating product/service descriptions",
        ],
        benefits=["Speeds up content and communication drafting"],
        recommend_when=["content creation", "drafting replies", "product description", "outdated content", "outdated blog"],
        do_not_recommend_when=[],
    ),

    ServiceDefinition(
        name="AI & ML",
        problems_solved=[
            "No way to predict trends or classify/prioritize incoming data at scale",
        ],
        target_businesses=["businesses with enough data volume to benefit from prediction/classification"],
        use_cases=[
            "Lead scoring / prioritization",
            "Demand forecasting",
        ],
        benefits=["Data-driven prioritization instead of guesswork"],
        recommend_when=["lead scoring", "prioritization", "forecasting", "predictive"],
        do_not_recommend_when=["very small business", "no data history"],
    ),

    ServiceDefinition(
        name="CRM Development",
        problems_solved=[
            "No central place to track leads/customers and their history",
        ],
        target_businesses=["businesses without an existing CRM managing more than a handful of leads"],
        use_cases=[
            "Custom lead/customer tracking system",
            "Sales pipeline management",
        ],
        benefits=["Central visibility into every lead/customer relationship"],
        recommend_when=["no crm", "manual lead intake", "client management", "no central tracking"],
        do_not_recommend_when=["already has crm or practice-management software"],
    ),

    ServiceDefinition(
        name="Workflow Automation",
        problems_solved=[
            "Manual handoffs between steps/teams that slow operations down",
        ],
        target_businesses=["businesses with multi-step internal processes"],
        use_cases=[
            "Automated task routing between teams",
            "Automated status notifications",
        ],
        benefits=["Removes manual handoff delays"],
        recommend_when=["manual handoff", "internal process", "staff handle manually", "routine scheduling requests"],
        do_not_recommend_when=[],
    ),

    ServiceDefinition(
        name="Full-Stack Web Development",
        problems_solved=[
            "Website is missing entirely, broken, or structurally inadequate",
        ],
        target_businesses=["businesses with no website, or a website with fundamental structural problems"],
        use_cases=[
            "New website build",
            "Website rebuild after structural/technical failure",
        ],
        benefits=["A working, correctly structured web presence to build everything else on"],
        recommend_when=["website unreachable", "website is not served over https", "no website", "website down"],
        do_not_recommend_when=["website already functional"],
    ),

    ServiceDefinition(
        name="E-commerce",
        problems_solved=[
            "No way to sell or take orders online",
        ],
        target_businesses=["restaurant", "retail", "product-based business"],
        use_cases=[
            "Online ordering system",
            "Online store setup",
        ],
        benefits=["Opens a direct online sales channel"],
        recommend_when=["no visible online appointment/booking system", "online ordering", "ecommerce", "online store", "sell online"],
        do_not_recommend_when=["service-only business with no product to sell online"],
    ),

    ServiceDefinition(
        name="Custom Business Applications",
        problems_solved=[
            "A specific operational need not covered by any off-the-shelf tool",
        ],
        target_businesses=["businesses with a specific process that generic tools do not fit"],
        use_cases=[
            "Purpose-built internal tools",
            "Custom business-process software",
        ],
        benefits=["Software that matches the business's exact process instead of forcing a generic tool"],
        recommend_when=["specific operational need", "custom process", "off-the-shelf tool does not fit"],
        do_not_recommend_when=["a standard off-the-shelf tool already fits the need"],
    ),

    ServiceDefinition(
        name="SaaS Development",
        problems_solved=[
            "A business's own solution could be productized into a subscription product for its market",
        ],
        target_businesses=["businesses positioned to sell software/tooling to others in their industry"],
        use_cases=[
            "Turning an internal tool into a sellable multi-tenant product",
        ],
        benefits=["New recurring-revenue product line"],
        recommend_when=["productize", "sell to other businesses", "multi-tenant", "subscription product"],
        do_not_recommend_when=["single-location small business with no product ambitions"],
    ),

    ServiceDefinition(
        name="Custom Software",
        problems_solved=[
            "General-purpose need that spans multiple of the above categories",
        ],
        target_businesses=["any business with a bespoke need not cleanly covered by a single specialized service"],
        use_cases=[
            "General bespoke software project",
        ],
        benefits=["Tailored solution when no narrower service fits well"],
        recommend_when=["bespoke", "general software need"],
        do_not_recommend_when=[],
    ),
]


def get_service(name: str) -> "ServiceDefinition | None":
    for svc in SERVICE_KNOWLEDGE_BASE:
        if svc.name == name:
            return svc
    return None
