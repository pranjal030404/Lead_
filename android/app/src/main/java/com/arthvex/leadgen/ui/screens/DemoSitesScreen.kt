package com.arthvex.leadgen.ui.screens

import android.content.Intent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.OpenInNew
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.arthvex.leadgen.ui.*
import com.arthvex.leadgen.ui.utils.openUrl

data class DemoSite(val niche: String, val name: String, val url: String)

/** Sample sites to show prospects, keyed by the niche they were built for. */
val DEMO_SITES = listOf(
    DemoSite("CA", "Chartered Accountant", "https://ca.demo.arthvex.co.in/"),
    DemoSite("Dry cleaner", "Dry Cleaner", "https://cleanthut.arthvex.co.in/"),
    DemoSite("Computer service", "Computer Service", "https://websites.demo.arthvex.co.in/"),
    DemoSite("Tiffin service", "Tiffin Service", "https://tiffin.arthvex.co.in/")
)

@Composable
fun DemoSitesScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    Scaffold(topBar = { BackBar("Demo websites", "Show prospects a finished site for their niche", onBack) }) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            DEMO_SITES.forEach { site ->
                SectionCard(site.name, site.url) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { openUrl(context, site.url) }) {
                            Icon(Icons.Default.OpenInNew, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("Open")
                        }
                        OutlinedButton(onClick = {
                            val send = Intent(Intent.ACTION_SEND).apply {
                                type = "text/plain"
                                putExtra(Intent.EXTRA_TEXT, site.url)
                            }
                            context.startActivity(Intent.createChooser(send, "Share demo link"))
                        }) { Text("Share") }
                    }
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}
