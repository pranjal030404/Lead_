package com.arthvex.leadgen.model

import kotlinx.serialization.Serializable

/** Follow-up tasks (GET /api/tasks). */
@Serializable
data class TaskItem(
    val id: Int = 0,
    val title: String = "",
    val notes: String? = null,
    val due_at: String? = null,
    val status: String = "OPEN",
    val lead_id: Int? = null
)

@Serializable
data class TasksResponse(val tasks: List<TaskItem> = emptyList(), val count: Int = 0)

/** Meetings booked with leads (GET /api/meetings). */
@Serializable
data class Meeting(
    val id: Int = 0,
    val lead_id: Int = 0,
    val title: String = "",
    val starts_at: String = "",
    val duration_min: Int = 30,
    val location: String? = null,
    val status: String = "SCHEDULED",
    val business_name: String? = null
)

@Serializable
data class MeetingsResponse(val meetings: List<Meeting> = emptyList())

/** Proposals / quotes (GET /api/proposals). `effective_status` also reports EXPIRED. */
@Serializable
data class Proposal(
    val id: Int = 0,
    val lead_id: Int = 0,
    val number: String = "",
    val title: String = "",
    val status: String = "DRAFT",
    val effective_status: String = "DRAFT",
    val currency: String = "INR",
    val total: Double = 0.0,
    val business_name: String? = null,
    val share_token: String = "",
    val valid_until: String? = null,
    val updated_at: String? = null
)

@Serializable
data class ProposalsResponse(val proposals: List<Proposal> = emptyList())

@Serializable
data class Revenue(
    val won_total: Double = 0.0,
    val open_value: Double = 0.0,
    val accepted: Int = 0,
    val declined: Int = 0,
    val win_rate: Double? = null
)
