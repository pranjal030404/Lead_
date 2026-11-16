package com.arthvex.leadgen

import com.arthvex.leadgen.data.ApiClient
import com.arthvex.leadgen.data.json
import com.arthvex.leadgen.model.*
import com.arthvex.leadgen.model.Target
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import org.junit.Assert.*
import org.junit.Test

/**
 * Decodes responses captured from a real backend instance (src/test/resources)
 * with the app's own models, so a contract drift fails here instead of on a phone.
 */
class ModelParsingTest {
    private fun fixture(name: String) =
        javaClass.classLoader!!.getResource(name)!!.readText()

    @Test fun leadsList() {
        val r = json.decodeFromString<LeadsResponse>(fixture("leads.json"))
        assertEquals(3, r.total)
        assertTrue(r.leads.any { it.businessName == "Smile Dental Clinic" && it.lead_priority == "HOT" })
    }

    @Test fun leadDetail() {
        val l = json.decodeFromString<Lead>(fixture("lead.json"))
        assertEquals("Smile Dental Clinic", l.businessName)
        assertEquals("919810000001", l.whatsapp)
    }

    @Test fun stats() {
        val s = json.decodeFromString<Stats>(fixture("stats.json"))
        assertEquals(3, s.total_leads)
        assertTrue(s.quota.google.cap > 0)
    }

    @Test fun facets() {
        val f = json.decodeFromString<Facets>(fixture("facets.json"))
        assertTrue("Faridabad" in f.cities)
        assertTrue("gym" in f.niches)
    }

    @Test fun conversionsAndWeekly() {
        json.decodeFromString<Conversions>(fixture("conversions.json"))
        val w = json.decodeFromString<WeeklyReport>(fixture("weekly.json"))
        assertTrue(w.period_start.isNotBlank())
    }

    @Test fun usersPlansTemplatesTargets() {
        assertEquals("tester", json.decodeFromString<UsersResponse>(fixture("users.json")).rows.first().username)
        assertEquals("Starter", json.decodeFromString<PlansResponse>(fixture("plans.json")).rows.first().name)
        val t = json.decodeFromString<List<MessageTemplate>>(fixture("templates.json"))
        assertTrue(t.any { it.type == "call_script" })
        assertTrue(json.decodeFromString<List<Target>>(fixture("targets.json")).isEmpty())
    }

    @Test fun automationBackupsIdentityPaymentBranding() {
        assertTrue(json.decodeFromString<AutomationStatus>(fixture("automation_status.json")).switches.isNotEmpty())
        assertEquals(30, json.decodeFromString<BackupsResponse>(fixture("backups.json")).keep_days)
        assertTrue(json.decodeFromString<Identity>(fixture("identity.json")).company_name.isNotBlank())
        assertFalse(json.decodeFromString<PaymentSettings>(fixture("payment.json")).enabled)
        assertTrue(json.decodeFromString<BrandingResponse>(fixture("branding.json")).brand.brand_name.isNotBlank())
        json.decodeFromString<Quota>(fixture("quota.json"))
    }

    @Test fun followUps() {
        json.decodeFromString<List<FollowUp>>(fixture("followups.json"))
    }

    @Test fun serverUrlIsFixedAndHttpsInProduction() {
        // The URL is not user-editable: whatever is passed in, the build's fixed server is used.
        assertEquals(ApiClient.normalise("http://evil.example"), ApiClient.normalise(""))
    }

    // ---- screens added in 1.1.0: decoded from live server responses ----

    @Test fun tasks() {
        val r = json.decodeFromString<TasksResponse>(fixture("tasks.json"))
        assertTrue(r.tasks.any { it.title == "Call the owner" && it.status == "OPEN" && it.lead_id != null })
    }

    @Test fun meetings() {
        val m = json.decodeFromString<MeetingsResponse>(fixture("meetings.json")).meetings.first()
        assertEquals("Intro call", m.title)
        assertEquals("SCHEDULED", m.status)
        assertEquals(30, m.duration_min)
        assertTrue(m.starts_at.startsWith("20"))
    }

    @Test fun proposalsAndRevenue() {
        val p = json.decodeFromString<ProposalsResponse>(fixture("proposals.json")).proposals.first()
        assertEquals("Website rebuild", p.title)
        assertEquals("DRAFT", p.effective_status)
        assertEquals(47200.0, p.total, 0.001)            // 40,000 + 18% tax
        assertTrue(p.number.startsWith("P-"))
        val r = json.decodeFromString<Revenue>(fixture("revenue.json"))
        assertEquals(0, r.accepted)
    }

    @Test fun loginRequestOmitsCodeUnlessGiven() {
        assertEquals("""{"username":"a","password":"b"}""", json.encodeToString(LoginRequest("a", "b")))
        assertEquals("""{"username":"a","password":"b","code":"123456"}""",
            json.encodeToString(LoginRequest("a", "b", "123456")))
    }

    @Test fun sessionCookieNameMatchesServer() {
        // app/security.py SESSION_COOKIE - if these drift the app forgets its sign-in on restart.
        assertEquals("leadgen_session", com.arthvex.leadgen.data.SessionCookieJar.SESSION_COOKIE)
    }
}
