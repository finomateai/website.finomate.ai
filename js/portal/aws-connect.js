(function () {
  function redirectToLogin() {
    window.location.replace("/login.html");
  }

  // ------------------------------------------------------------------
  // Auth guard
  // ------------------------------------------------------------------
  function isTokenValid(token) {
    try {
      const payload = JSON.parse(
        atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
      );
      return payload.exp && payload.exp > Math.floor(Date.now() / 1000);
    } catch {
      return false;
    }
  }

  const token = localStorage.getItem("finomate_token");
  if (!token || !isTokenValid(token)) {
    redirectToLogin();
    return;
  }

  // Populate destination bucket ARN for Role B policy guidance
  try {
    const jwtPayload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    const clientId = jwtPayload.client_id;
    if (!clientId) {
      redirectToLogin();
      return;
    }
    document.getElementById("dest-bucket-arn").textContent = `arn:aws:s3:::${clientId}-billing-data/*`;
  } catch {
    redirectToLogin();
    return;
  }

  document
    .getElementById("sign-out-btn")
    .addEventListener("click", function () {
      localStorage.removeItem("finomate_token");
      redirectToLogin();
    });

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------
  let accessMethod = "assumeRole"; // or "directIAM"
  let testPassed = false;
  let testRunning = false;

  const CREDENTIAL_FIELDS = [
    "roleArn",
    "roleBArn",
    "accessKeyId",
    "secretAccessKey",
  ];

  // ------------------------------------------------------------------
  // Access method toggle
  // ------------------------------------------------------------------
  document.querySelectorAll(".method-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      accessMethod = btn.dataset.method;
      document.querySelectorAll(".method-toggle").forEach(function (b) {
        b.classList.toggle("active", b.dataset.method === accessMethod);
      });
      document
        .getElementById("fields-assumeRole")
        .classList.toggle("hidden", accessMethod !== "assumeRole");
      document
        .getElementById("fields-directIAM")
        .classList.toggle("hidden", accessMethod !== "directIAM");

      // Update check list labels
      const checkItems = document.querySelectorAll(".check-item");
      if (accessMethod === "directIAM") {
        checkItems[0].style.display = "none"; // role assumption skipped
        checkItems[2].style.display = "none"; // versioning skipped
      } else {
        checkItems[0].querySelector(".label").textContent =
          "Assume cross-account role (Role A)";
        checkItems[0].style.display = "";
        checkItems[2].style.display = "";
      }

      resetTest();
      updateTestBtn();
    });
  });

  // Set initial active state
  document.getElementById("toggle-assume").classList.add("active");

  // ------------------------------------------------------------------
  // Field validation helpers
  // ------------------------------------------------------------------
  function validateIamArn(val) {
    return val.trim().startsWith("arn:aws:iam::");
  }
  function validateS3Arn(val) {
    return val.trim().startsWith("arn:aws:s3:::");
  }
  function bucketNameFromArn(arn) {
    if (!arn || typeof arn !== "string") return null;

    const trimmed = arn.trim();

    // Must strictly match S3 bucket ARN format
    if (!trimmed.startsWith("arn:aws:s3:::")) return null;

    const resource = trimmed.slice("arn:aws:s3:::".length);

    // Reject object ARNs or anything with '/'
    if (resource.includes("/")) return null;

    // Reject empty bucket name
    if (!resource) return null;

    return resource;
  }

  document.getElementById("bucketArn").addEventListener("input", function () {
    const err = document.getElementById("bucketArn-error");
    const val = this.value.trim();
    const invalid = val && (!validateS3Arn(val) || bucketNameFromArn(val) === null);
    this.classList.toggle("error", !!invalid);
    err.classList.toggle("hidden", !invalid);
    resetTest();
    updateTestBtn();
    updateSubmitBtn();
  });

  document.getElementById("roleArn").addEventListener("input", function () {
    const err = document.getElementById("roleArn-error");
    const val = this.value.trim();
    if (val && !validateIamArn(val)) {
      this.classList.add("error");
      err.classList.remove("hidden");
    } else {
      this.classList.remove("error");
      err.classList.add("hidden");
    }
    resetTest();
    updateTestBtn();
  });

  document.getElementById("roleBArn").addEventListener("input", function () {
    const err = document.getElementById("roleBArn-error");
    const val = this.value.trim();
    if (val && !validateIamArn(val)) {
      this.classList.add("error");
      err.classList.remove("hidden");
    } else {
      this.classList.remove("error");
      err.classList.add("hidden");
    }
    resetTest();
    updateTestBtn();
  });

  // Credential fields — reset test if edited after passing
  CREDENTIAL_FIELDS.forEach(function (id) {
    const el = document.getElementById(id);
    if (el)
      el.addEventListener("input", function () {
        resetTest();
        updateTestBtn();
        updateSubmitBtn();
      });
  });

  // Other required fields
  ["companyName", "email"].forEach(function (id) {
    const el = document.getElementById(id);
    if (el)
      el.addEventListener("change", function () {
        updateTestBtn();
        updateSubmitBtn();
      });
    if (el)
      el.addEventListener("input", function () {
        updateTestBtn();
        updateSubmitBtn();
      });
  });

  document
    .getElementById("curFormatConfirm")
    .addEventListener("change", function () {
      updateSubmitBtn();
    });

  document
    .getElementById("ackCheckbox")
    .addEventListener("change", function () {
      updateSubmitBtn();
    });

  // ------------------------------------------------------------------
  // companyName — auto-sanitize to lowercase, no spaces
  // ------------------------------------------------------------------
  document.getElementById("companyName").addEventListener("input", function () {
    this.value = this.value
      .toLowerCase()
      .replace(/\s+/g, "-")
      .replace(/[^a-z0-9-]/g, "");
  });

  // ------------------------------------------------------------------
  // Test button enable/disable logic
  // ------------------------------------------------------------------
  function isTestReady() {
    const bucketName = bucketNameFromArn(document.getElementById("bucketArn").value);

    if (!bucketName) return false;

    if (accessMethod === "assumeRole") {
      const roleArn = document.getElementById("roleArn").value.trim();
      const roleBArn = document.getElementById("roleBArn").value.trim();
      return validateIamArn(roleArn) && validateIamArn(roleBArn);
    } else {
      const keyId = document.getElementById("accessKeyId").value.trim();
      const secret = document.getElementById("secretAccessKey").value.trim();
      return keyId.length > 0 && secret.length > 0;
    }
  }

  function updateTestBtn() {
    document.getElementById("test-btn").disabled =
      !isTestReady() || testRunning;
  }

  function updateSubmitBtn() {
    const allFilled =
      document.getElementById("companyName").value.trim() &&
      document.getElementById("email").value.trim() &&
      bucketNameFromArn(document.getElementById("bucketArn").value) &&
      document.getElementById("curFormatConfirm").checked &&
      document.getElementById("ackCheckbox").checked &&
      testPassed;
    document.getElementById("submit-btn").disabled = !allFilled;
  }

  // ------------------------------------------------------------------
  // Reset test UI
  // ------------------------------------------------------------------
  function resetTest() {
    if (testRunning) return;
    testPassed = false;
    document.querySelectorAll(".check-item").forEach(function (el) {
      el.className = el.className
        .replace(/\b(running|passed|failed)\b/g, "")
        .trim();
      el.classList.add("pending");
      el.querySelector(".label").classList.remove(
        "text-slate-900",
        "text-red-600",
      );
      el.querySelector(".label").classList.add("text-slate-500");
    });
    document.querySelectorAll(".check-error").forEach(function (el) {
      el.remove();
    });
    document.getElementById("test-result").classList.add("hidden");
    updateSubmitBtn();
  }

  // ------------------------------------------------------------------
  // Test connection
  // ------------------------------------------------------------------
  document
    .getElementById("test-btn")
    .addEventListener("click", async function () {
      if (!isTestReady()) return;
      resetTest();
      testRunning = true;
      updateTestBtn();

      const testIcon = document.getElementById("test-icon");
      const testSpinner = document.getElementById("test-spinner");
      const testBtnText = document.getElementById("test-btn-text");
      testIcon.classList.add("hidden");
      testSpinner.classList.remove("hidden");
      testBtnText.textContent = "Testing…";

      const payload = {
        bucketName: bucketNameFromArn(document.getElementById("bucketArn").value),
        accessMethod: accessMethod,
      };

      if (accessMethod === "assumeRole") {
        payload.roleArn = document.getElementById("roleArn").value.trim();
        payload.roleBArn = document.getElementById("roleBArn").value.trim();
      } else {
        payload.accessKeyId = document
          .getElementById("accessKeyId")
          .value.trim();
        payload.secretAccessKey = document
          .getElementById("secretAccessKey")
          .value.trim();
      }

      const checkItems = document.querySelectorAll(".check-item");

      let data;
      try {
        const resp = await fetch(`${API_BASE}/v1/aws-connect/test`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(payload),
        });
        data = await resp.json();
      } catch (err) {
        showTestResult(false, "Network error — could not reach the server");
        resetTestButtons();
        return;
      }

      const checks = data.checks || [];

      // Animate checks in sequence with small delay
      for (let i = 0; i < checks.length; i++) {
        const check = checks[i];
        // directIAM: skip slot 0 (role) and slot 2 (versioning)
        // checks[0] → slot 1, checks[1] → slot 3
        const domIdx = accessMethod === "directIAM" ? (i === 0 ? 1 : 3) : i;
        const item = checkItems[domIdx];
        if (!item) continue;

        item.classList.remove("pending");
        item.classList.add("running");
        await delay(300);

        item.classList.remove("running");
        if (check.passed) {
          item.classList.add("passed");
          item.querySelector(".label").classList.remove("text-slate-500");
          item.querySelector(".label").classList.add("text-slate-900");
        } else {
          item.classList.add("failed");
          item.querySelector(".label").classList.remove("text-slate-500");
          item.querySelector(".label").classList.add("text-red-600");
          if (check.error && check.error !== "Skipped") {
            const sub = document.createElement("p");
            sub.className = "check-error text-xs text-red-500 mt-0.5 ml-5";
            sub.textContent = check.error;
            item.after(sub);
          }
        }
      }

      testPassed = data.status === "ok";
      showTestResult(
        testPassed,
        testPassed
          ? "All checks passed — connection verified"
          : "One or more checks failed. See errors above.",
      );
      resetTestButtons();
      updateSubmitBtn();
    });

  function delay(ms) {
    return new Promise(function (r) {
      setTimeout(r, ms);
    });
  }

  function showTestResult(passed, message) {
    const el = document.getElementById("test-result");
    el.classList.remove("hidden");
    if (passed) {
      el.className =
        "mt-4 px-4 py-3 rounded-lg flex items-center gap-2 text-sm font-medium bg-green-50 border border-green-200 text-green-800";
      el.innerHTML = `<svg class="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>
        <span>${message}</span>`;
    } else {
      el.className =
        "mt-4 px-4 py-3 rounded-lg flex items-center gap-2 text-sm font-medium bg-red-50 border border-red-200 text-red-800";
      el.innerHTML = `<svg class="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 20 20">
        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/></svg>
        <span>${message}</span>`;
    }
  }

  function resetTestButtons() {
    testRunning = false;
    document.getElementById("test-icon").classList.remove("hidden");
    document.getElementById("test-spinner").classList.add("hidden");
    document.getElementById("test-btn-text").textContent = testPassed
      ? "Re-test"
      : "Test connection";
    updateTestBtn();
  }

  // ------------------------------------------------------------------
  // Submit
  // ------------------------------------------------------------------
  document
    .getElementById("submit-btn")
    .addEventListener("click", async function () {
      const btn = this;
      btn.disabled = true;
      document.getElementById("submit-icon").classList.add("hidden");
      document.getElementById("submit-spinner").classList.remove("hidden");
      document.getElementById("submit-btn-text").textContent =
        "Setting up connector…";

      const progressContainer = document.getElementById("submit-progress");
      const progressSteps = document.getElementById("progress-steps");
      progressContainer.classList.remove("hidden");
      progressSteps.innerHTML = "";

      function addStep(label, state) {
        const colors = {
          running: "text-slate-500",
          success: "text-green-700",
          failed: "text-red-600",
          skipped: "text-slate-400",
        };
        const icons = {
          running: `<svg class="w-4 h-4 animate-spin text-brand-500" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>`,
          success: `<svg class="w-4 h-4 text-green-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>`,
          failed: `<svg class="w-4 h-4 text-red-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"/></svg>`,
          skipped: `<svg class="w-4 h-4 text-slate-300" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 001 1h3a1 1 0 100-2h-2V6z" clip-rule="evenodd"/></svg>`,
        };
        const div = document.createElement("div");
        div.className = `flex items-center gap-2.5 text-sm ${colors[state] || "text-slate-500"}`;
        div.innerHTML = `${icons[state] || icons.running}<span>${label}</span>`;
        progressSteps.appendChild(div);
        return div;
      }

      const STEP_LABELS = {
        create_bucket: "Creating destination S3 bucket",
        write_connector_json: "Writing connector.json",
        attach_bucket_policy: "Attaching destination bucket policy",
        write_replication_rule: "Configuring live replication rule",
        trigger_batch_copy: "Triggering historical data copy",
      };

      // Show running indicators
      Object.values(STEP_LABELS).forEach(function (label) {
        addStep(label, "running");
      });

      const bucketArn = document.getElementById("bucketArn").value.trim();
      const payload = {
        companyName: document.getElementById("companyName").value.trim(),
        email: document.getElementById("email").value.trim(),
        bucketName: bucketNameFromArn(bucketArn),
        bucketArn: bucketArn,
        accessMethod: accessMethod,
      };

      if (accessMethod === "assumeRole") {
        payload.roleArn = document.getElementById("roleArn").value.trim();
        payload.roleBArn = document.getElementById("roleBArn").value.trim();
      } else {
        payload.accessKeyId = document
          .getElementById("accessKeyId")
          .value.trim();
        payload.secretAccessKey = document
          .getElementById("secretAccessKey")
          .value.trim();
      }

      let data;
      try {
        const resp = await fetch(`${API_BASE}/v1/aws-connect/submit`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify(payload),
        });
        data = await resp.json();
      } catch (err) {
        showSubmitResult(false, "Network error — could not reach the server");
        resetSubmitBtn();
        return;
      }

      // Replace running indicators with real results
      progressSteps.innerHTML = "";
      if (data.steps && data.steps.length > 0) {
        data.steps.forEach(function (step) {
          const label = STEP_LABELS[step.step] || step.step;
          const state =
            step.status === "success"
              ? "success"
              : step.status === "skipped"
                ? "skipped"
                : step.status === "failed"
                  ? "failed"
                  : "success";
          const div = addStep(label, state);
          if (step.jobId)
            div.querySelector("span").textContent += ` (Job ID: ${step.jobId})`;
          if (step.error) {
            const err = document.createElement("p");
            err.className = "text-xs text-red-500 ml-6 mt-0.5";
            err.textContent = step.error;
            div.after(err);
          }
        });
      }

      const success = data.status === "success";
      if (success) {
        showSubmitResult(
          true,
          `<strong>Connector created successfully.</strong>
           <br
           Your billing data will start replicating automatically.`,
        );
      } else {
        showSubmitResult(
          false,
          data.error || "Connector setup failed. Check step details above.",
        );
        btn.disabled = false;
      }

      resetSubmitBtn(success);
    });

  function showSubmitResult(success, html) {
    const el = document.getElementById("submit-result");
    el.classList.remove("hidden");
    if (success) {
      el.className =
        "mt-5 px-4 py-4 rounded-xl bg-green-50 border border-green-200 text-green-800 text-sm";
    } else {
      el.className =
        "mt-5 px-4 py-4 rounded-xl bg-red-50 border border-red-200 text-red-800 text-sm";
    }
    el.innerHTML = html;
  }

  function resetSubmitBtn(success) {
    document.getElementById("submit-icon").classList.remove("hidden");
    document.getElementById("submit-spinner").classList.add("hidden");
    document.getElementById("submit-btn-text").textContent = success
      ? "Connector Connected"
      : "Retry";
  }
})();
