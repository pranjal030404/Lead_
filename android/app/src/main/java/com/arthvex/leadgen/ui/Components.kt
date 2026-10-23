package com.arthvex.leadgen.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.Message
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.model.Lead
import com.arthvex.leadgen.ui.utils.prettyDate

val HotColor = Color(0xFFDC2626)
val WarmColor = Color(0xFFD97706)
val ColdColor = Color(0xFF64748B)
val GoodColor = Color(0xFF15803D)
val InfoColor = Color(0xFF1D4ED8)
val PurpleColor = Color(0xFF7C3AED)

fun priorityColor(priority: String?): Color = when (priority?.uppercase()) {
    "HOT" -> HotColor
    "WARM" -> WarmColor
    else -> ColdColor
}

fun statusColor(status: String?): Color = when (status?.uppercase()) {
    "NEW" -> InfoColor
    "CONTACTED" -> PurpleColor
    "REPLIED" -> Color(0xFF0891B2)
    "MEETING_SET" -> Color(0xFF0D9488)
    "PROPOSAL_SENT" -> WarmColor
    "NEGOTIATION" -> Color(0xFFC2410C)
    "WON" -> GoodColor
    "LOST" -> ColdColor
    else -> ColdColor
}

fun statusLabel(status: String?): String = when (status?.uppercase()) {
    "MEETING_SET" -> "Meeting set"
    "PROPOSAL_SENT" -> "Proposal sent"
    "NEW" -> "New"
    "CONTACTED" -> "Contacted"
    "REPLIED" -> "Replied"
    "NEGOTIATION" -> "Negotiation"
    "WON" -> "Won"
    "LOST" -> "Lost"
    else -> status ?: "Unknown"
}

@Composable
fun Pill(text: String, color: Color, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .background(color.copy(alpha = 0.13f), RoundedCornerShape(50))
            .padding(horizontal = 9.dp, vertical = 3.dp)
    ) {
        Text(
            text,
            color = color,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.SemiBold,
            maxLines = 1
        )
    }
}

@Composable
fun ScoreBadge(score: Int?) {
    val value = score ?: 0
    val color = when {
        value >= 8 -> GoodColor
        value >= 5 -> WarmColor
        else -> ColdColor
    }
    Box(
        modifier = Modifier
            .size(42.dp)
            .background(color.copy(alpha = 0.14f), CircleShape),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("$value", color = color, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
            Text("/10", color = color.copy(alpha = 0.8f), style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
fun LeadCard(
    lead: Lead,
    onOpen: () -> Unit,
    onCall: () -> Unit,
    onWhatsApp: () -> Unit
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onOpen),
        shape = RoundedCornerShape(16.dp)
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                ScoreBadge(lead.lead_score)
                Spacer(Modifier.width(12.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        lead.businessName ?: "Unnamed business",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis
                    )
                    val place = listOfNotNull(lead.area, lead.city).filter { it.isNotBlank() }.distinct()
                        .joinToString(", ")
                    if (place.isNotBlank()) {
                        Text(
                            place,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1
                        )
                    }
                    Spacer(Modifier.height(6.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Pill(lead.lead_priority ?: "COLD", priorityColor(lead.lead_priority))
                        Pill(statusLabel(lead.status), statusColor(lead.status))
                    }
                }
                Icon(
                    Icons.Default.ChevronRight,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(
                    onClick = onCall,
                    modifier = Modifier.weight(1f),
                    enabled = !lead.phone.isNullOrBlank()
                ) {
                    Icon(Icons.Default.Call, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(5.dp))
                    Text("Call")
                }
                Button(
                    onClick = onWhatsApp,
                    modifier = Modifier.weight(1f),
                    enabled = !(lead.whatsapp ?: lead.phone).isNullOrBlank()
                ) {
                    Icon(Icons.Default.Message, contentDescription = null, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(5.dp))
                    Text("WhatsApp")
                }
            }
            if (!lead.follow_up_date.isNullOrBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(
                    "Follow-up · ${prettyDate(lead.follow_up_date)}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary
                )
            }
        }
    }
}

@Composable
fun StatTile(label: String, value: String, accent: Color, modifier: Modifier = Modifier) {
    Card(modifier = modifier, shape = RoundedCornerShape(16.dp)) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Spacer(Modifier.height(4.dp))
            Text(value, style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold, color = accent)
        }
    }
}

@Composable
fun EmptyState(title: String, subtitle: String) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(6.dp))
        Text(
            subtitle,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = androidx.compose.ui.text.style.TextAlign.Center
        )
    }
}
