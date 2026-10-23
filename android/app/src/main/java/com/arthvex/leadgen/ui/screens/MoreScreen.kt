package com.arthvex.leadgen.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.model.User
import com.arthvex.leadgen.ui.*

private data class MoreItem(
    val route: String,
    val label: String,
    val hint: String,
    val icon: ImageVector,
    val color: Color
)

private data class MoreGroup(val title: String, val items: List<MoreItem>)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MoreScreen(
    user: User,
    onNavigate: (String) -> Unit,
    onLoggedOut: () -> Unit
) {
    val isAdmin = user.role == "admin" || user.role == "superadmin"

    val groups = buildList {
        add(
            MoreGroup(
                "Pipeline",
                listOf(
                    MoreItem("search", "Search", "Find new businesses", Icons.Default.Search, InfoColor),
                    MoreItem("map", "Map", "Leads by location", Icons.Default.Place, Color(0xFF0891B2)),
                    MoreItem("pipeline", "Pipeline", "Deals by stage", Icons.Default.ViewKanban, PurpleColor),
                    MoreItem("coverage", "Coverage", "What you've searched", Icons.Default.Public, GoodColor),
                    MoreItem("reports", "Reports", "Weekly performance", Icons.Default.BarChart, WarmColor)
                )
            )
        )
        add(
            MoreGroup(
                "Automation",
                listOf(
                    MoreItem("approvals", "Approve sends", "Review before sending", Icons.Default.Send, HotColor),
                    MoreItem("automation", "Control", "Switches and jobs", Icons.Default.Tune, ColdColor)
                )
            )
        )
        add(
            MoreGroup(
                "Setup",
                listOf(
                    MoreItem("myplan", "My Plan", "Subscription and usage", Icons.Default.CreditCard, PurpleColor),
                    MoreItem("targets", "Targets", "Saved niche + city", Icons.Default.TrackChanges, InfoColor),
                    MoreItem("templates", "Templates", "Message copy", Icons.Default.Description, WarmColor)
                )
            )
        )
        if (isAdmin) {
            add(
                MoreGroup(
                    "Administration",
                    listOf(
                        MoreItem("users", "Users", "Accounts and roles", Icons.Default.Group, InfoColor),
                        MoreItem("plans", "Plans", "Pricing and orders", Icons.Default.Layers, GoodColor)
                    )
                )
            )
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("More", fontWeight = FontWeight.Bold)
                        Text(
                            user.full_name?.takeIf { it.isNotBlank() } ?: user.username,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            )
        }
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            groups.forEach { group ->
                Text(
                    group.title.uppercase(),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = FontWeight.Bold
                )
                Card(shape = RoundedCornerShape(16.dp)) {
                    Column {
                        group.items.forEachIndexed { index, item ->
                            Row(
                                Modifier
                                    .fillMaxWidth()
                                    .clickable { onNavigate(item.route) }
                                    .padding(horizontal = 14.dp, vertical = 12.dp),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Box(
                                    Modifier
                                        .size(38.dp)
                                        .background(item.color.copy(alpha = 0.14f), RoundedCornerShape(11.dp)),
                                    contentAlignment = Alignment.Center
                                ) {
                                    Icon(item.icon, contentDescription = null, tint = item.color, modifier = Modifier.size(20.dp))
                                }
                                Spacer(Modifier.width(12.dp))
                                Column(Modifier.weight(1f)) {
                                    Text(item.label, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
                                    Text(
                                        item.hint,
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                }
                                Icon(
                                    Icons.Default.ChevronRight,
                                    contentDescription = null,
                                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                                )
                            }
                            if (index < group.items.lastIndex) {
                                HorizontalDivider(modifier = Modifier.padding(start = 64.dp))
                            }
                        }
                    }
                }
            }

            Text(
                "Account".uppercase(),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontWeight = FontWeight.Bold
            )
            Card(shape = RoundedCornerShape(16.dp)) {
                Column {
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clickable { onNavigate("settings") }
                            .padding(horizontal = 14.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            Modifier
                                .size(38.dp)
                                .background(ColdColor.copy(alpha = 0.14f), RoundedCornerShape(11.dp)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(Icons.Default.Settings, contentDescription = null, tint = ColdColor, modifier = Modifier.size(20.dp))
                        }
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("Settings", style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
                            Text(
                                "Identity, branding, account",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                        Icon(Icons.Default.ChevronRight, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    HorizontalDivider(modifier = Modifier.padding(start = 64.dp))
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clickable { onLoggedOut() }
                            .padding(horizontal = 14.dp, vertical = 12.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            Modifier
                                .size(38.dp)
                                .background(HotColor.copy(alpha = 0.12f), RoundedCornerShape(11.dp)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(Icons.AutoMirrored.Filled.Logout, contentDescription = null, tint = HotColor, modifier = Modifier.size(20.dp))
                        }
                        Spacer(Modifier.width(12.dp))
                        Text("Sign out", style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium)
                    }
                }
            }
        }
    }
}
