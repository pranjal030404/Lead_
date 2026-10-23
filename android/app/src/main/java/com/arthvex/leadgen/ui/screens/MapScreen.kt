package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.nativeCanvas
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.GeoLead
import com.arthvex.leadgen.ui.*
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MapScreen(repo: ApiRepo, onBack: () -> Unit, onOpenLead: (Int) -> Unit) {
    val scope = rememberCoroutineScope()
    var leads by remember { mutableStateOf<List<GeoLead>>(emptyList()) }
    var count by remember { mutableStateOf(0) }
    var priority by remember { mutableStateOf<String?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var selected by remember { mutableStateOf<GeoLead?>(null) }

    fun load() {
        loading = true
        scope.launch {
            repo.geoMap(priority).onSuccess {
                leads = it.leads
                count = it.count
                selected = null
                error = null
            }.onFailure { error = it.message }
            loading = false
        }
    }

    LaunchedEffect(priority) { load() }

    Scaffold(
        topBar = { BackBar("Map", "$count lead(s) with coordinates", onBack) { load() } }
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(null to "All", "HOT" to "Hot", "WARM" to "Warm", "COLD" to "Cold")
                    .forEach { (value, label) ->
                        FilterChip(
                            selected = priority == value,
                            onClick = { priority = value },
                            label = { Text(label) }
                        )
                    }
            }
            LoadingBar(loading)
            error?.let { ErrorCard(it) { load() } }

            if (leads.isEmpty()) {
                if (!loading) {
                    EmptyCard(
                        "No mapped leads",
                        "Leads get coordinates when a search returns a business with a location. Run a search, then come back."
                    )
                }
            } else {
                LeadMap(
                    leads = leads,
                    selected = selected,
                    onSelect = { selected = it }
                )
                Text(
                    "Tap a dot to see the lead. The map is schematic — it plots the coordinates the provider returned, not street tiles.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )

                selected?.let { lead ->
                    SectionCard(
                        title = lead.business_name ?: "Lead",
                        subtitle = listOfNotNull(lead.area, lead.city).filter { it.isNotBlank() }.joinToString(", ")
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            ScoreBadge(lead.lead_score)
                            Spacer(Modifier.width(10.dp))
                            Column(Modifier.weight(1f)) {
                                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                    Pill(lead.lead_priority ?: "COLD", priorityColor(lead.lead_priority))
                                    StatusPill(lead.status)
                                }
                                Spacer(Modifier.height(4.dp))
                                Text(lead.phone ?: "No phone", style = MaterialTheme.typography.bodySmall)
                            }
                            Button(onClick = { onOpenLead(lead.id) }) { Text("Open") }
                        }
                    }
                }

                SectionCard("Leads on this map") {
                    leads.take(50).forEach { lead ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .padding(vertical = 5.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Box(
                                Modifier
                                    .size(10.dp)
                                    .background(priorityColor(lead.lead_priority), RoundedCornerShape(50))
                            )
                            Spacer(Modifier.width(10.dp))
                            Column(Modifier.weight(1f)) {
                                Text(
                                    lead.business_name ?: "Unnamed",
                                    style = MaterialTheme.typography.bodyMedium,
                                    maxLines = 1
                                )
                                Text(
                                    lead.city ?: "",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                            TextButton(onClick = { onOpenLead(lead.id) }) { Text("Open") }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun LeadMap(leads: List<GeoLead>, selected: GeoLead?, onSelect: (GeoLead?) -> Unit) {
    val background = MaterialTheme.colorScheme.surfaceVariant
    val grid = MaterialTheme.colorScheme.outlineVariant
    val pinned = remember(leads) {
        if (leads.isEmpty()) {
            GeoBounds(-90.0, 90.0, -180.0, 180.0)
        } else {
            val lats = leads.map { it.lat }
            val lngs = leads.map { it.lng }
            val minLat = lats.min(); val maxLat = lats.max()
            val minLng = lngs.min(); val maxLng = lngs.max()
            GeoBounds(
                minLat = minLat - ((maxLat - minLat).coerceAtLeast(0.01) * 0.12),
                maxLat = maxLat + ((maxLat - minLat).coerceAtLeast(0.01) * 0.12),
                minLng = minLng - ((maxLng - minLng).coerceAtLeast(0.01) * 0.12),
                maxLng = maxLng + ((maxLng - minLng).coerceAtLeast(0.01) * 0.12)
            )
        }
    }

    Canvas(
        Modifier
            .fillMaxWidth()
            .height(340.dp)
            .background(background, RoundedCornerShape(18.dp))
            .pointerInput(leads, pinned) {
                detectTapGestures { tap ->
                    val width = size.width.toFloat()
                    val height = size.height.toFloat()
                    val nearest = leads.map { lead ->
                        val point = project(lead, pinned, width, height)
                        lead to kotlin.math.hypot(point.x - tap.x, point.y - tap.y)
                    }.filter { it.second < 60f }.minByOrNull { it.second }
                    onSelect(nearest?.first)
                }
            }
    ) {
        val stepX = size.width / 6f
        val stepY = size.height / 5f
        for (i in 1..5) {
            drawLine(grid.copy(alpha = 0.5f), Offset(i * stepX, 0f), Offset(i * stepX, size.height), 1f)
        }
        for (i in 1..4) {
            drawLine(grid.copy(alpha = 0.5f), Offset(0f, i * stepY), Offset(size.width, i * stepY), 1f)
        }
        leads.forEach { lead ->
            val point = project(lead, pinned, size.width, size.height)
            val color = priorityColor(lead.lead_priority)
            val radius = 5f + (lead.lead_score ?: 0) * 0.9f
            if (selected?.id == lead.id) {
                drawCircle(color.copy(alpha = 0.22f), radius * 2.4f, point)
                drawCircle(Color.White, radius + 2f, point, style = Stroke(width = 2f))
            }
            drawCircle(color.copy(alpha = 0.80f), radius, point)
            drawCircle(color, radius, point, style = Stroke(width = 1.4f))
        }
        if (leads.isEmpty()) {
            drawContext.canvas.nativeCanvas.drawText(
                "No coordinates yet",
                size.width / 2 - 55f,
                size.height / 2,
                android.graphics.Paint().apply {
                    color = android.graphics.Color.GRAY
                    textSize = 32f
                }
            )
        }
    }
    Spacer(Modifier.height(6.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        listOf("HOT" to HotColor, "WARM" to WarmColor, "COLD" to ColdColor).forEach { (label, color) ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(9.dp).background(color, RoundedCornerShape(50)))
                Spacer(Modifier.width(4.dp))
                Text(label, style = MaterialTheme.typography.labelSmall, fontSize = 11.sp)
            }
        }
    }
}

private data class GeoBounds(
    val minLat: Double,
    val maxLat: Double,
    val minLng: Double,
    val maxLng: Double
)

private fun project(lead: GeoLead, bounds: GeoBounds, width: Float, height: Float): Offset {
    val x = ((lead.lng - bounds.minLng) / (bounds.maxLng - bounds.minLng).coerceAtLeast(0.0001)).toFloat()
    val y = ((lead.lat - bounds.minLat) / (bounds.maxLat - bounds.minLat).coerceAtLeast(0.0001)).toFloat()
    val padding = 18f
    return Offset(
        padding + x * (width - padding * 2),
        height - padding - y * (height - padding * 2)
    )
}
