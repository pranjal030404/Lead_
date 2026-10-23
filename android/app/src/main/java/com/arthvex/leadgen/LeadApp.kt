package com.arthvex.leadgen

import android.app.Application
import com.arthvex.leadgen.data.ApiClient
import com.arthvex.leadgen.data.ApiRepo
import com.arthvex.leadgen.data.Prefs

class LeadApp : Application() {
    lateinit var prefs: Prefs
        private set
    lateinit var repo: ApiRepo
        private set

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        repo = ApiRepo(ApiClient(prefs))
    }
}
