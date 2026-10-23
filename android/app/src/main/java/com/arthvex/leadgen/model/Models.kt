package com.arthvex.leadgen.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class User(
    val id: Int = 0,
    val username: String = "",
    val role: String = "",
    val email: String? = null,
    val full_name: String? = null
)

@Serializable
data class MeResponse(
    val user: User? = null
)

@Serializable
data class LoginRequest(
    val username: String,
    val password: String
)

@Serializable
data class LoginResponse(
    val ok: Boolean = false,
    val user: User? = null
)

@Serializable
data class LeadsResponse(
    val total: Int = 0,
    val limit: Int = 0,
    val offset: Int = 0,
    val leads: List<Lead> = emptyList()
)

@Serializable
data class Lead(
    val id: Int = 0,
    @SerialName("business_name") val businessName: String? = null,
    val owner_name: String? = null,
    val phone: String? = null,
    val email: String? = null,
    val whatsapp: String? = null,
    val website_url: String? = null,
    val address: String? = null,
    val area: String? = null,
    val city: String? = null,
    val state: String? = null,
    val country: String? = null,
    val pincode: String? = null,
    val niche: String? = null,
    val status: String? = null,
    val lead_priority: String? = null,
    val lead_score: Int? = null,
    val data_quality_score: Int? = null,
    val overall_score: Int? = null,
    val intent_score: Int? = null,
    val opportunity_tier: String? = null,
    val opportunity_count: Int? = null,
    val tags: String? = null,
    val notes: String? = null,
    val follow_up_date: String? = null,
    val first_contacted_date: String? = null,
    val last_contacted_date: String? = null,
    val total_touchpoints: Int? = null,
    val contact_method: String? = null,
    val enrichment_status: String? = null,
    val instagram_url: String? = null,
    val facebook_url: String? = null,
    val linkedin_url: String? = null,
    val google_rating: Double? = null,
    val google_reviews: Int? = null,
    val google_maps_url: String? = null,
    val score_reasons: List<String> = emptyList(),
    val website_issues: List<String> = emptyList(),
    val interactions: List<Interaction> = emptyList(),
    val queued_messages: List<Approval> = emptyList(),
    // Website analysis (populated by enrichment)
    val website_score: Int? = null,
    val website_platform: String? = null,
    val website_ssl: Int? = null,
    val website_mobile_friendly: Int? = null,
    val website_speed_ms: Int? = null,
    val website_last_updated: String? = null,
    val website_checked_at: String? = null,
    val found_date: String? = null,
    val phone_valid: Int? = null,
    val enrichment_error: String? = null,
    val source: String? = null
)

@Serializable
data class Interaction(
    val id: Int = 0,
    val lead_id: Int = 0,
    val type: String = "",
    val direction: String = "",
    val date: String = "",
    val subject: String? = null,
    val content: String? = null,
    val outcome: String? = null,
    val next_step: String? = null,
    val follow_up_date: String? = null,
    val automated: Int = 0
)

@Serializable
data class InteractionRequest(
    val type: String,
    val subject: String? = null,
    val content: String? = null,
    val outcome: String? = null,
    val next_step: String? = null,
    val follow_up_date: String? = null,
    val new_status: String? = null
)

@Serializable
data class MessageResponse(
    val subject: String? = null,
    val body: String = "",
    val to: String? = null,
    val mailto: String? = null,
    val whatsapp_link: String? = null
)

@Serializable
data class MessageTemplate(
    val id: Int = 0,
    val name: String = "",
    val type: String = "",
    val stage: String? = null,
    val subject: String? = null,
    val body: String = "",
    val target_market: String = "india",
    val language: String = "en"
)

@Serializable
data class FollowUp(
    val id: Int = 0,
    @SerialName("business_name") val businessName: String? = null,
    val city: String? = null,
    val phone: String? = null,
    val email: String? = null,
    val whatsapp: String? = null,
    val status: String? = null,
    val lead_score: Int? = null,
    val lead_priority: String? = null,
    val follow_up_date: String? = null,
    val last_contacted_date: String? = null,
    val total_touchpoints: Int? = null,
    val days_since_contact: Int? = null,
    val step: String? = null,
    val action: String? = null
)

@Serializable
data class CountRow(
    val priority: String? = null,
    val status: String? = null,
    val n: Int = 0
)

@Serializable
data class Stats(
    val total_leads: Int = 0,
    val hot_leads: Int = 0,
    val warm_leads: Int = 0,
    val no_website: Int = 0,
    val contacted: Int = 0,
    val won: Int = 0,
    val won_this_month: Int = 0,
    val follow_ups_due: Int = 0,
    val pending_approval: Int = 0,
    val new_this_week: Int = 0,
    val by_status: List<CountRow> = emptyList(),
    val by_priority: List<CountRow> = emptyList(),
    val pending_enrichment: Int = 0,
    val unresolved_failures: Int = 0,
    val by_niche: List<BreakdownRow> = emptyList(),
    val by_city: List<BreakdownRow> = emptyList(),
    val quota: Quota = Quota()
)

@Serializable
data class BreakdownRow(
    val niche: String? = null,
    val city: String? = null,
    val n: Int = 0,
    val hot: Int = 0
)

@Serializable
data class ApiError(
    val detail: String = ""
)
