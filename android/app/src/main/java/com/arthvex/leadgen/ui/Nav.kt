package com.arthvex.leadgen.ui

import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Apps
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.People
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.arthvex.leadgen.LeadApp
import com.arthvex.leadgen.model.User
import com.arthvex.leadgen.ui.screens.*
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.launch

private sealed interface Session {
    data object Booting : Session
    data object LoggedOut : Session
    data class Offline(val message: String) : Session
    data class LoggedIn(val user: User) : Session
}

@Composable
fun AppNav() {
    LeadTheme {
        val app = LocalContext.current.applicationContext as LeadApp
        val repo = app.repo
        val prefs = app.prefs
        var session by remember { mutableStateOf<Session>(Session.Booting) }

        var bootTry by remember { mutableStateOf(0) }
        LaunchedEffect(bootTry) {
            session = Session.Booting
            repo.me()
                .onSuccess { user -> session = if (user != null) Session.LoggedIn(user) else Session.LoggedOut }
                .onFailure { e ->
                    // A network problem must not look like a sign-out: the stored
                    // session is still valid, so offer a retry instead.
                    session = if (e is com.arthvex.leadgen.data.ApiException && e.status in 400..499) Session.LoggedOut
                    else Session.Offline(e.message ?: "Could not reach the server")
                }
        }
        DisposableEffect(repo) {
            val main = android.os.Handler(android.os.Looper.getMainLooper())
            repo.onUnauthorized = { main.post { session = Session.LoggedOut } }
            onDispose { repo.onUnauthorized = null }
        }

        when (val current = session) {
            Session.Booting -> BootScreen()
            is Session.Offline -> Surface(Modifier.fillMaxSize()) {
                Column(
                    Modifier.fillMaxSize().padding(28.dp),
                    verticalArrangement = Arrangement.Center,
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    Text("Can't reach LeadGen", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Spacer(Modifier.height(8.dp))
                    Text(current.message, style = MaterialTheme.typography.bodyMedium)
                    Spacer(Modifier.height(16.dp))
                    Button(onClick = { bootTry++ }) { Text("Retry") }
                }
            }
            Session.LoggedOut -> LoginScreen(
                repo = repo,
                prefs = prefs,
                onLoggedIn = { session = Session.LoggedIn(it) }
            )
            is Session.LoggedIn -> HomeShell(
                repo = repo,
                prefs = prefs,
                user = current.user,
                onLoggedOut = { session = Session.LoggedOut }
            )
        }
    }
}

@Composable
private fun BootScreen() {
    Surface(Modifier.fillMaxSize()) {
        Column(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally
        ) {
            CircularProgressIndicator()
            Spacer(Modifier.height(14.dp))
            Text("Arthvex LeadGen", fontWeight = FontWeight.SemiBold)
            Text(
                "Connecting to your workspace…",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}

private data class TabItem(
    val route: String,
    val label: String,
    val icon: androidx.compose.ui.graphics.vector.ImageVector
)

private val TABS = listOf(
    TabItem("stats", "Dashboard", Icons.Default.Insights),
    TabItem("leads", "Leads", Icons.Default.People),
    TabItem("followups", "Follow-ups", Icons.Default.Schedule),
    TabItem("more", "More", Icons.Default.Apps)
)

private val TAB_ROUTES = TABS.map { it.route }.toSet()

@Composable
private fun HomeShell(
    repo: com.arthvex.leadgen.data.ApiRepo,
    prefs: com.arthvex.leadgen.data.Prefs,
    user: User,
    onLoggedOut: () -> Unit
) {
    val navController = rememberNavController()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val route = backStackEntry?.destination?.route
    val showBottomBar = route in TAB_ROUTES
    var autoLog by remember { mutableStateOf(prefs.autoLog) }
    val scope = rememberCoroutineScope()
    val signOut: () -> Unit = {
        scope.launch { repo.logout() }
        onLoggedOut()
    }

    Scaffold(
        bottomBar = {
            if (showBottomBar) {
                NavigationBar {
                    TABS.forEach { tab ->
                        NavigationBarItem(
                            selected = route == tab.route,
                            onClick = {
                                navController.navigate(tab.route) {
                                    popUpTo(navController.graph.findStartDestination().id) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(tab.icon, contentDescription = tab.label) },
                            label = { Text(tab.label) }
                        )
                    }
                }
            }
        }
    ) { padding ->
        val back: () -> Unit = { navController.popBackStack() }
        val openLead: (Int) -> Unit = { id -> navController.navigate("lead/$id") }

        NavHost(
            navController = navController,
            startDestination = "stats",
            modifier = Modifier.padding(padding)
        ) {
            composable("stats") {
                StatsScreen(
                    repo = repo,
                    onOpenLead = openLead,
                    onOpen = { navController.navigate(it) }
                )
            }
            composable("leads") {
                LeadsScreen(repo = repo, autoLog = autoLog, onOpenLead = openLead)
            }
            composable("followups") {
                FollowUpsScreen(repo = repo, autoLog = autoLog, onOpenLead = openLead)
            }
            composable("more") {
                MoreScreen(
                    user = user,
                    onNavigate = { navController.navigate(it) },
                    onLoggedOut = signOut
                )
            }

            composable("search") { SearchScreen(repo = repo, onBack = back, onOpenLead = openLead) }
            composable("map") { MapScreen(repo = repo, onBack = back, onOpenLead = openLead) }
            composable("pipeline") { PipelineScreen(repo = repo, onBack = back, onOpenLead = openLead) }
            composable("coverage") { CoverageScreen(repo = repo, onBack = back) }
            composable("approvals") { ApprovalsScreen(repo = repo, onBack = back, onOpenLead = openLead) }
            composable("automation") { AutomationScreen(repo = repo, onBack = back) }
            composable("reports") { ReportsScreen(repo = repo, onBack = back) }
            composable("myplan") { MyPlanScreen(repo = repo, onBack = back) }
            composable("targets") { TargetsScreen(repo = repo, onBack = back) }
            composable("templates") { TemplatesScreen(repo = repo, onBack = back) }
            composable("users") { UsersScreen(repo = repo, onBack = back) }
            composable("plans") { PlansScreen(repo = repo, onBack = back) }
            composable("settings") {
                SettingsScreen(
                    repo = repo,
                    prefs = prefs,
                    user = user,
                    onAutoLogChanged = { autoLog = it },
                    onLoggedOut = signOut,
                    onBack = back
                )
            }
            composable(
                "lead/{leadId}",
                arguments = listOf(navArgument("leadId") { type = NavType.IntType })
            ) { entry ->
                val leadId = entry.arguments?.getInt("leadId") ?: 0
                LeadDetailScreen(
                    repo = repo,
                    leadId = leadId,
                    autoLog = autoLog,
                    onBack = back
                )
            }
        }
    }
}
