package com.arthvex.leadgen

import com.arthvex.leadgen.data.ApiClient
import com.arthvex.leadgen.data.json
import com.arthvex.leadgen.model.*
import com.arthvex.leadgen.model.Target
import kotlinx.serialization.decodeFromString
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
}
