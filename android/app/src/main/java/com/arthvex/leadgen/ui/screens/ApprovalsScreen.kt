package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.Approval
import com.arthvex.leadgen.model.Identity
import com.arthvex.leadgen.ui.*
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ApprovalsScreen(repo: ApiRepo, onBack: () -> Unit, onOpenLead: (Int) -> Unit) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var approvals by remember { mutableStateOf<List<Approval>>(emptyList()) }
    var identity by remember { mutableStateOf<Identity?>(null) }
    var loading by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var filter by remember { mutableStateOf("PENDING") }

    fun load() {
        loading = true
        scope.launch {
            repo.approvals(filter).onSuccess {
                approvals = it
                error = null
            }.onFailure { error = it.message }
            repo.identity().onSuccess { identity = it }
            loading = false
        }
    }

    LaunchedEffect(filter) { load() }

    fun act(block: suspend () -> String) {
        busy = true
        scope.launch {
            runCatching { block() }
                .onSuccess { snackbar.showSnackbar(it) }
                .onFailure { snackbar.showSnackbar(it.message ?: "Could not complete that") }
            busy = false
            load()
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = { BackBar("Approve sends", "Nothing leaves until you approve it", onBack) { load() } }
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            LoadingBar(loading || busy)
            error?.let { ErrorCard(it) { load() } }

            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                listOf("PENDING" to "Pending", "APPROVED" to "Approved", "SENT" to "Sent")
                    .forEach { (value, label) ->
                        FilterChip(
                            selected = filter == value,
                            onClick = { filter = value },
                            label = { Text(label) }
                        )
                    }
            }

            identity?.let { id ->
                if (id.dry_run && filter == "PENDING") {
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 12.dp),
                        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.tertiaryContainer)
                    ) {
                        Text(
                            "Dry-run mode is on. Approved messages are logged, not sent.",
                            modifier = Modifier.padding(12.dp),
                            style = MaterialTheme.typography.bodySmall
                        )
                    }
                }
            }

            if (filter == "PENDING" && approvals.isNotEmpty()) {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    OutlinedButton(
                        onClick = {
                            act {
                                val ids = approvals.map { it.id }
                                val approved = repo.approveAll(ids).getOrThrow()
                                "$approved approved"
                            }
                        },
                        enabled = !busy,
                        modifier = Modifier.weight(1f)
                    ) { Text("Approve all") }
                    Button(
                        onClick = {
                            act {
                                val result = repo.sendApproved().getOrThrow()
                                "Sent ${result.sent}, failed ${result.failed}" +
                                    if (result.dry_run == true) " (dry run)" else ""
                            }
                        },
                        enabled = !busy,
                        modifier = Modifier.weight(1f)
                    ) { Text("Send approved") }
                }
            }

            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            onClick = {
                                act {
                                    val result = repo.generateFollowUps().getOrThrow()
                                    "${result.generated} generated, ${result.marked_lost} marked lost"
                                }
                            },
                            enabled = !busy,
                            modifier = Modifier.weight(1f)
                        ) { Text("Generate follow-ups") }
                    }
                }

                items(approvals, key = { it.id }) { message ->
                    ApprovalCard(
                        message = message,
                        busy = busy,
                        onOpenLead = { onOpenLead(message.lead_id) },
                        onApprove = { subject, body ->
                            act {
                                repo.editApproval(message.id, subject, body).getOrThrow()
                                repo.approve(message.id).getOrThrow()
                                "Approved"
                            }
                        },
                        onSkip = {
                            act {
                                repo.skipApproval(message.id).getOrThrow()
                                "Skipped"
                            }
                        }
                    )
                }

                if (approvals.isEmpty() && !loading) {
                    item {
                        EmptyCard(
                            if (filter == "PENDING") "Nothing waiting for approval" else "Nothing here",
                            if (filter == "PENDING")
                                "Generate due follow-ups, or queue a message from a lead's Outreach tab."
                            else "No messages with this status."
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ApprovalCard(
    message: Approval,
    busy: Boolean,
    onOpenLead: () -> Unit,
    onApprove: (String?, String) -> Unit,
    onSkip: () -> Unit
) {
    var subject by remember(message.id, message.subject) { mutableStateOf(message.subject.orEmpty()) }
    var body by remember(message.id, message.body) { mutableStateOf(message.body) }

    Card(shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        message.business_name ?: "Lead",
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        listOfNotNull(message.city, message.step, message.template_name)
                            .filter { it.isNotBlank() }.joinToString(" · "),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
                Pill(message.lead_priority ?: "COLD", priorityColor(message.lead_priority))
            }
            Spacer(Modifier.height(4.dp))
            Text(
                message.to_address ?: "no address",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Spacer(Modifier.height(8.dp))
            if (!message.subject.isNullOrBlank()) {
                OutlinedTextField(
                    value = subject,
                    onValueChange = { subject = it },
                    label = { Text("Subject") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    enabled = message.status == "PENDING"
                )
                Spacer(Modifier.height(8.dp))
            }
            OutlinedTextField(
                value = body,
                onValueChange = { body = it },
                label = { Text("Body") },
                modifier = Modifier.fillMaxWidth(),
                minLines = 5,
                enabled = message.status == "PENDING"
            )
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                if (message.status == "PENDING") {
                    Button(
                        onClick = { onApprove(subject.ifBlank { null }, body) },
                        enabled = !busy
                    ) { Text("Approve") }
                    OutlinedButton(onClick = onSkip, enabled = !busy) { Text("Skip") }
                } else {
                    StatusPill(message.status)
                    if (!message.sent_at.isNullOrBlank()) {
                        Text(
                            message.sent_at.take(16).replace('T', ' '),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
                Spacer(Modifier.weight(1f))
                TextButton(onClick = onOpenLead) { Text("Open lead") }
            }
            if (!message.error.isNullOrBlank()) {
                Text(
                    message.error,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error
                )
            }
        }
    }
}
