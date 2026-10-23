package com.arthvex.leadgen.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Message
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.model.Lead
import com.arthvex.leadgen.model.MessageTemplate
import com.arthvex.leadgen.ui.utils.sendWhatsApp
import kotlinx.coroutines.launch

fun defaultWhatsAppMessage(lead: Lead): String {
    val name = lead.owner_name?.trim()?.takeIf { it.isNotBlank() } ?: "there"
    val business = lead.businessName?.trim()?.takeIf { it.isNotBlank() }
    val city = lead.city?.trim()?.takeIf { it.isNotBlank() }
    val contextLine = when {
        business != null && city != null -> "I came across $business in $city."
        business != null -> "I came across $business."
        city != null -> "I came across your business in $city."
        else -> "I came across your business."
    }
    return "Hi $name, $contextLine I build fast, mobile-friendly websites that help " +
        "local businesses get more enquiries. Would you be open to a quick chat?"
}

/**
 * Compose a WhatsApp message, open the chat with it pre-filled and (optionally)
 * write a WHATSAPP touchpoint back to the CRM. WhatsApp has no public API for
 * silent sends, so the final "send" tap stays with the user by design.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WhatsAppComposerSheet(
    lead: Lead,
    repo: ApiRepo,
    autoLog: Boolean,
    onDismiss: () -> Unit,
    onStatus: (String) -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var message by remember(lead.id) { mutableStateOf(defaultWhatsAppMessage(lead)) }
    var templates by remember { mutableStateOf<List<MessageTemplate>>(emptyList()) }
    var selectedTemplate by remember { mutableStateOf<String?>(null) }
    var loadingTemplates by remember { mutableStateOf(true) }
    var generating by remember { mutableStateOf(false) }

    LaunchedEffect(lead.id) {
        repo.templates("whatsapp").onSuccess { list ->
            templates = list
            if (list.isNotEmpty()) {
                selectedTemplate = list.first().name
            }
        }
        loadingTemplates = false
    }

    fun applyTemplate(template: MessageTemplate) {
        selectedTemplate = template.name
        generating = true
        scope.launch {
            repo.renderMessage(lead.id, template.name).onSuccess { message = it.body }
                .onFailure { onStatus("Could not render template; kept current text") }
            generating = false
        }
    }

    val number = lead.whatsapp ?: lead.phone

    ModalBottomSheet(onDismissRequest = onDismiss, sheetState = sheetState) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(start = 20.dp, end = 20.dp, bottom = 28.dp)
        ) {
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Icon(Icons.Default.Message, contentDescription = null, tint = GoodColor)
                Spacer(Modifier.width(8.dp))
                Column {
                    Text(
                        "WhatsApp message",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        lead.businessName ?: "Lead",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }

            Spacer(Modifier.height(6.dp))
            Text(
                if (number.isNullOrBlank()) "No WhatsApp-capable number on this lead"
                else "To +$number",
                style = MaterialTheme.typography.labelMedium,
                color = if (number.isNullOrBlank()) MaterialTheme.colorScheme.error
                else MaterialTheme.colorScheme.primary
            )

            Spacer(Modifier.height(14.dp))
            if (loadingTemplates) {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            } else if (templates.isNotEmpty()) {
                Text(
                    "Templates",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(Modifier.height(6.dp))
                Row(
                    modifier = Modifier.horizontalScroll(rememberScrollState()),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    templates.forEach { template ->
                        FilterChip(
                            selected = selectedTemplate == template.name,
                            onClick = { applyTemplate(template) },
                            label = { Text(template.name) }
                        )
                    }
                }
                Spacer(Modifier.height(12.dp))
            }

            OutlinedTextField(
                value = message,
                onValueChange = { message = it },
                label = { Text("Message") },
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 140.dp),
                minLines = 5,
                maxLines = 10,
                supportingText = { Text("${message.length} characters") }
            )

            Spacer(Modifier.height(6.dp))
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Text(
                    if (autoLog) "Will be logged to the CRM automatically"
                    else "Auto-logging is off (Settings)",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }

            if (generating) {
                Spacer(Modifier.height(8.dp))
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth())
            }

            Spacer(Modifier.height(16.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(
                    onClick = {
                        if (message.isBlank()) {
                            Toast.makeText(context, "Write a message first", Toast.LENGTH_SHORT).show()
                            return@Button
                        }
                        sendWhatsApp(context, repo, lead, message, autoLog, scope, onStatus)
                        onDismiss()
                    },
                    modifier = Modifier.weight(1f),
                    enabled = !number.isNullOrBlank() && message.isNotBlank()
                ) {
                    Icon(Icons.Default.Message, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Open in WhatsApp")
                }
                OutlinedButton(
                    onClick = {
                        copyToClipboard(context, message)
                        onStatus("Message copied")
                    }
                ) {
                    Text("Copy")
                }
            }

            Spacer(Modifier.height(6.dp))
            TextButton(
                onClick = { message = defaultWhatsAppMessage(lead) },
                modifier = Modifier.fillMaxWidth()
            ) {
                Text("Reset to default message")
            }
        }
    }
}

fun copyToClipboard(context: Context, text: String) {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    clipboard.setPrimaryClip(ClipData.newPlainText("WhatsApp message", text))
    Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
}
