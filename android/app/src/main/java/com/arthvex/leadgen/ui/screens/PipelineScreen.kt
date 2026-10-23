package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.PipelineStage
import com.arthvex.leadgen.ui.*
import com.arthvex.leadgen.ui.utils.relativeFollowUp
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun PipelineScreen(repo: ApiRepo, onBack: () -> Unit, onOpenLead: (Int) -> Unit) {
    val scope = rememberCoroutineScope()
    var stages by remember { mutableStateOf<List<PipelineStage>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }

    fun load() {
        loading = true
        scope.launch {
            repo.pipeline().onSuccess {
                stages = it
                error = null
            }.onFailure { error = it.message }
            loading = false
        }
    }

    LaunchedEffect(Unit) { load() }

    Scaffold(
        topBar = { BackBar("Pipeline", "Every deal, by stage", onBack) { load() } }
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            LoadingBar(loading)
            error?.let { ErrorCard(it) { load() } }
            LazyRow(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(12.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                items(stages, key = { it.id }) { stage ->
                    Card(
                        modifier = Modifier
                            .width(250.dp)
                            .fillMaxHeight(),
                        shape = RoundedCornerShape(16.dp)
                    ) {
                        Column(Modifier.fillMaxSize()) {
                            Row(
                                Modifier
                                    .fillMaxWidth()
                                    .padding(12.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text(
                                    stage.name.replace('_', ' '),
                                    style = MaterialTheme.typography.titleSmall,
                                    fontWeight = FontWeight.Bold,
                                    color = parseColor(stage.color)
                                )
                                Pill("${stage.count}", parseColor(stage.color))
                            }
                            HorizontalDivider()
                            LazyColumn(
                                modifier = Modifier.fillMaxSize(),
                                contentPadding = PaddingValues(10.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                items(stage.leads, key = { it.id }) { lead ->
                                    Card(
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .clickable { onOpenLead(lead.id) },
                                        shape = RoundedCornerShape(12.dp),
                                        colors = CardDefaults.cardColors(
                                            containerColor = MaterialTheme.colorScheme.surfaceVariant
                                        )
                                    ) {
                                        Column(Modifier.padding(10.dp)) {
                                            Row(verticalAlignment = Alignment.CenterVertically) {
                                                Box(
                                                    Modifier
                                                        .size(8.dp)
                                                        .background(
                                                            priorityColor(lead.lead_priority),
                                                            RoundedCornerShape(50)
                                                        )
                                                )
                                                Spacer(Modifier.width(6.dp))
                                                Text(
                                                    lead.business_name ?: "Unnamed",
                                                    style = MaterialTheme.typography.bodyMedium,
                                                    fontWeight = FontWeight.Medium,
                                                    maxLines = 1,
                                                    modifier = Modifier.weight(1f)
                                                )
                                                Text(
                                                    "${lead.lead_score ?: 0}",
                                                    style = MaterialTheme.typography.labelSmall,
                                                    color = priorityColor(lead.lead_priority)
                                                )
                                            }
                                            val place = lead.city.orEmpty()
                                            if (place.isNotBlank()) {
                                                Text(
                                                    place,
                                                    style = MaterialTheme.typography.labelSmall,
                                                    color = MaterialTheme.colorScheme.onSurfaceVariant
                                                )
                                            }
                                            if (!lead.follow_up_date.isNullOrBlank()) {
                                                Text(
                                                    relativeFollowUp(lead.follow_up_date),
                                                    style = MaterialTheme.typography.labelSmall,
                                                    color = MaterialTheme.colorScheme.primary
                                                )
                                            }
                                        }
                                    }
                                }
                                if (stage.leads.isEmpty()) {
                                    item {
                                        Text(
                                            "Empty",
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

fun parseColor(hex: String): Color = try {
    val clean = hex.trim().removePrefix("#")
    when (clean.length) {
        6 -> Color(("FF$clean").toLong(16))
        8 -> Color(clean.toLong(16))
        else -> ColdColor
    }
} catch (_: Exception) {
    ColdColor
}
