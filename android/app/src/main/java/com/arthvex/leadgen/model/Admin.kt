package com.arthvex.leadgen.model

import kotlinx.serialization.Serializable

// ---------------------------------------------------------------------------
// Models for the secondary API surfaces: search, map, pipeline, coverage,
// approvals, automation, reports, plans, targets, users, settings.
// Everything has defaults so a version skew on the server degrades to blanks
// instead of failing the whole screen.
// ---------------------------------------------------------------------------

@Serializable
data class ProviderOption(
    val key: String = "",
    val label: String = "",
    val ready: Boolean = false
)

@Serializable
data class ProvidersResponse(
    val active: String = "",
    val available: List<ProviderOption> = emptyList()
)

@Serializable
data class GoogleQuota(
    val used_today: Int = 0,
    val cap: Int = 0,
    val remaining: Int = 0,
    val pct: Int = 0,
    val warning: Boolean = false,
    val by_tier: Map<String, Int> = emptyMap()
)

@Serializable
data class HunterQuota(
    val used_this_month: Int = 0,
    val cap: Int = 0,
    val remaining: Int = 0
)

@Serializable
data class Quota(
    val google: GoogleQuota = GoogleQuota(),
    val hunter: HunterQuota = HunterQuota(),
    val provider: String = ""
)

@Serializable
data class SearchStart(
    val search_id: Int = 0,
    val status: String = "",
    val city: String = "",
    val area: String = "",
    val radius_m: Int? = null
)

@Serializable
data class SearchLead(
    val id: Int = 0,
    val business_name: String? = null,
    val phone: String? = null,
    val website_url: String? = null,
    val lead_score: Int? = null,
    val lead_priority: String? = null,
    val city: String? = null
)

@Serializable
data class SearchRun(
    val id: Int = 0,
    val niche: String? = null,
    val city: String? = null,
    val area: String? = null,
    val country: String? = null,
    val provider: String? = null,
    val status: String = "",
    val results_found: Int = 0,
    val new_leads: Int = 0,
    val duplicates_skipped: Int = 0,
    val hot_count: Int = 0,
    val from_cache: Boolean = false,
    val cache_source_search_id: Int? = null,
    val progress: String? = null,
    val error_message: String? = null,
    val started_at: String? = null,
    val completed_at: String? = null,
    val leads: List<SearchLead> = emptyList()
)

@Serializable
data class SearchPreview(
    val coverage: CoverageCell = CoverageCell(),
    val message: String = ""
)

@Serializable
data class CoverageCell(
    val id: Int = 0,
    val city: String? = null,
    val area: String? = null,
    val country: String? = null,
    val niche: String? = null,
    val times_searched: Int = 0,
    val total_leads_found: Int = 0,
    val total_duplicates: Int = 0,
    val last_searched: String? = null,
    val status: String = "NEVER"
)

@Serializable
data class Coverage(
    val rows: List<CoverageCell> = emptyList(),
    val cities: List<String> = emptyList(),
    val niches: List<String> = emptyList()
)

@Serializable
data class GeoLead(
    val id: Int = 0,
    val business_name: String? = null,
    val lat: Double = 0.0,
    val lng: Double = 0.0,
    val lead_score: Int? = null,
    val lead_priority: String? = null,
    val status: String? = null,
    val city: String? = null,
    val area: String? = null,
    val phone: String? = null,
    val has_website: Int? = null,
    val website_url: String? = null,
    val niche: String? = null
)

@Serializable
data class GeoMap(
    val count: Int = 0,
    val leads: List<GeoLead> = emptyList()
)

@Serializable
data class PipelineLead(
    val id: Int = 0,
    val business_name: String? = null,
    val city: String? = null,
    val lead_score: Int? = null,
    val lead_priority: String? = null,
    val phone: String? = null,
    val email: String? = null,
    val last_contacted_date: String? = null,
    val follow_up_date: String? = null
)

@Serializable
data class PipelineStage(
    val id: Int = 0,
    val name: String = "",
    val color: String = "",
    val position: Int = 0,
    val count: Int = 0,
    val leads: List<PipelineLead> = emptyList()
)

@Serializable
data class FunnelStep(
    val from: String = "",
    val to: String = "",
    val from_count: Int = 0,
    val to_count: Int = 0,
    val rate: Double = 0.0
)

@Serializable
data class Conversions(
    val reached: Map<String, Int> = emptyMap(),
    val steps: List<FunnelStep> = emptyList(),
    val search_to_lead: Double = 0.0,
    val search_to_won: Double = 0.0,
    val results_seen: Int = 0
)

@Serializable
data class WeeklyRow(
    val niche: String? = null,
    val city: String? = null,
    val leads: Int = 0,
    val contacted: Int = 0,
    val won: Int = 0
)

@Serializable
data class CostPerHotLead(
    val api_calls_this_month: Int = 0,
    val hot_leads_this_month: Int = 0,
    val calls_per_hot_lead: Double? = null,
    val note: String = ""
)

@Serializable
data class WeeklyReport(
    val period_start: String = "",
    val period_end: String = "",
    val leads_found: Int = 0,
    val hot_found: Int = 0,
    val searches_run: Int = 0,
    val total_touchpoints: Int = 0,
    val meetings_set: Int = 0,
    val deals_won: Int = 0,
    val outreach: Map<String, Int> = emptyMap(),
    val by_niche: List<WeeklyRow> = emptyList(),
    val by_city: List<WeeklyRow> = emptyList(),
    val cost_per_hot_lead: CostPerHotLead = CostPerHotLead()
)

@Serializable
data class Facets(
    val cities: List<String> = emptyList(),
    val niches: List<String> = emptyList(),
    val countries: List<String> = emptyList(),
    val statuses: List<String> = emptyList(),
    val sources: List<String> = emptyList()
)

@Serializable
data class Approval(
    val id: Int = 0,
    val lead_id: Int = 0,
    val channel: String = "",
    val template_name: String? = null,
    val step: String? = null,
    val to_address: String? = null,
    val subject: String? = null,
    val body: String = "",
    val status: String = "",
    val created_at: String? = null,
    val sent_at: String? = null,
    val error: String? = null,
    val business_name: String? = null,
    val city: String? = null,
    val lead_priority: String? = null,
    val lead_score: Int? = null,
    val phone: String? = null
)

@Serializable
data class ApprovalEdit(
    val subject: String? = null,
    val body: String? = null
)

@Serializable
data class BulkApprove(val ids: List<Int>)

@Serializable
data class GenerateResult(
    val generated: Int = 0,
    val marked_lost: Int = 0
)

@Serializable
data class SendResult(
    val sent: Int = 0,
    val failed: Int = 0,
    val skipped: Int = 0,
    val note: String? = null,
    val dry_run: Boolean? = null
)

@Serializable
data class AutomationToday(
    val searches: Int = 0,
    val leads_found: Int = 0,
    val enriched: Int = 0,
    val awaiting_approval: Int = 0,
    val sent: Int = 0
)

@Serializable
data class AutomationJob(
    val id: String = "",
    val next_run: String? = null
)

@Serializable
data class AutomationStatus(
    val switches: Map<String, Boolean> = emptyMap(),
    val jobs: List<AutomationJob> = emptyList(),
    val dry_run: Boolean = false,
    val provider: String = "",
    val today: AutomationToday = AutomationToday(),
    val unresolved_failures: Int = 0
)

@Serializable
data class AutomationLogEntry(
    val id: Int = 0,
    val job: String = "",
    val level: String = "",
    val message: String = "",
    val created_at: String? = null
)

@Serializable
data class Failure(
    val id: Int = 0,
    val job: String = "",
    val error: String = "",
    val created_at: String? = null,
    val resolved: Int = 0
)

@Serializable
data class Backup(
    val name: String = "",
    val size_kb: Long = 0,
    val created: String? = null
)

@Serializable
data class BackupsResponse(
    val backups: List<Backup> = emptyList(),
    val keep_days: Int = 0
)

@Serializable
data class BackupResult(
    val name: String = "",
    val size_kb: Long = 0,
    val created: String? = null
)

@Serializable
data class Target(
    val id: Int = 0,
    val niche: String = "",
    val city: String = "",
    val area: String? = null,
    val country: String? = null,
    val max_results: Int = 20,
    val is_active: Int = 1,
    val auto_search: String = "weekly",
    val last_searched: String? = null,
    val next_search_date: String? = null,
    val coverage: CoverageCell = CoverageCell()
)

@Serializable
data class TargetRequest(
    val niche: String,
    val city: String,
    val area: String = "",
    val country: String = "",
    val max_results: Int = 20,
    val auto_search: String = "weekly"
)

@Serializable
data class Plan(
    val id: Int = 0,
    val name: String = "",
    val description: String = "",
    val price: Double = 0.0,
    val currency: String = "INR",
    val period_days: Int = 30,
    val search_limit: Int = 50,
    val max_results_per_search: Int = 20,
    val is_active: Int = 1
)

@Serializable
data class PlansResponse(val rows: List<Plan> = emptyList())

@Serializable
data class Subscription(
    val id: Int = 0,
    val plan_id: Int = 0,
    val plan_name: String = "",
    val status: String = "",
    val period_start: String? = null,
    val period_end: String? = null,
    val searches_used: Int = 0,
    val search_limit: Int = 0,
    val max_results_per_search: Int = 0,
    val channel: String? = null,
    val payment_status: String? = null,
    val price: Double = 0.0,
    val currency: String = "INR",
    val created_at: String? = null,
    val username: String? = null,
    val full_name: String? = null
)

@Serializable
data class MySubscription(val subscription: Subscription? = null)

@Serializable
data class PaymentSettings(
    val enabled: Boolean = false,
    val active_subscriptions: Int = 0,
    val pending_orders: Int = 0
)

@Serializable
data class UserRow(
    val id: Int = 0,
    val username: String = "",
    val full_name: String? = null,
    val email: String? = null,
    val role: String = "user",
    val is_active: Int = 1,
    val created_at: String? = null
)

@Serializable
data class UsersResponse(
    val rows: List<UserRow> = emptyList(),
    val total: Int = 0
)

@Serializable
data class UserPayload(
    val username: String,
    val full_name: String = "",
    val email: String = "",
    val password: String = "",
    val role: String = "user",
    val is_active: Boolean = true
)

@Serializable
data class AssignPlanRequest(
    val plan_id: Int,
    val period_days: Int? = null
)

@Serializable
data class Identity(
    val company_name: String = "",
    val portfolio_url: String = "",
    val sender_name: String = "",
    val sender_email: String = "",
    val dry_run: Boolean = false,
    val brand_name: String = ""
)

@Serializable
data class IdentityRequest(
    val company_name: String? = null,
    val portfolio_url: String? = null
)

@Serializable
data class SuppressionEntry(
    val id: Int = 0,
    val value: String = "",
    val kind: String = "email",
    val reason: String = "",
    val created_at: String? = null
)

@Serializable
data class Brand(
    val brand_name: String = "",
    val brand_short_name: String = "",
    val brand_tagline: String = "",
    val brand_accent: String = "",
    val brand_accent_2: String = "",
    val brand_background: String = "",
    val brand_landing_title: String = "",
    val brand_landing_subtitle: String = "",
    val brand_company_name: String = "",
    val brand_support_email: String = "",
    val brand_footer: String = "",
    val brand_logo: String? = null
)

@Serializable
data class BrandingResponse(
    val brand: Brand = Brand(),
    val overridden: List<String> = emptyList()
)

@Serializable
data class TemplateRequest(
    val name: String,
    val type: String = "email",
    val stage: String? = null,
    val subject: String? = null,
    val body: String,
    val target_market: String = "india",
    val language: String = "en"
)
