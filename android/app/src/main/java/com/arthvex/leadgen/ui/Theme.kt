package com.arthvex.leadgen.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val Teal = Color(0xFF0F766E)
private val TealLight = Color(0xFF5EEAD4)

private val LightColors = lightColorScheme(
    primary = Teal,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFCCFBF1),
    onPrimaryContainer = Color(0xFF134E4A),
    secondary = Color(0xFF1D4ED8),
    secondaryContainer = Color(0xFFDBEAFE),
    tertiary = Color(0xFF15803D),
    background = Color(0xFFF7F8F7),
    surface = Color(0xFFFFFFFF),
    surfaceVariant = Color(0xFFEDF1F0),
    error = Color(0xFFB91C1C)
)

private val DarkColors = darkColorScheme(
    primary = TealLight,
    onPrimary = Color(0xFF003731),
    primaryContainer = Color(0xFF115E59),
    onPrimaryContainer = Color(0xFFCCFBF1),
    secondary = Color(0xFF93C5FD),
    secondaryContainer = Color(0xFF1E3A8A),
    tertiary = Color(0xFF86EFAC),
    background = Color(0xFF0B1210),
    surface = Color(0xFF121A18),
    surfaceVariant = Color(0xFF1E2A27),
    error = Color(0xFFFCA5A5)
)

@Composable
fun LeadTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors,
        content = content
    )
}
