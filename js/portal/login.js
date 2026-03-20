// If already authenticated with a valid token, redirect immediately
const existing = localStorage.getItem("finomate_token");
if (existing && isTokenValid(existing)) {
  window.location.replace("/aws-connect.html");
}

const form = document.getElementById("login-form");
const submitBtn = document.getElementById("submit-btn");
const btnText = document.getElementById("btn-text");
const btnSpinner = document.getElementById("btn-spinner");
const errorMsg = document.getElementById("error-msg");
const errorText = document.getElementById("error-text");

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

function showError(msg) {
  errorText.textContent = msg;
  errorMsg.classList.remove("hidden");
}

function hideError() {
  errorMsg.classList.add("hidden");
}

function setLoading(loading) {
  submitBtn.disabled = loading;
  btnText.textContent = loading ? "Signing in…" : "Sign in";
  btnSpinner.classList.toggle("hidden", !loading);
}

form.addEventListener("submit", async function (e) {
  e.preventDefault();
  hideError();

  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;

  if (!username || !password) {
    showError("Please enter your username and password.");
    return;
  }

  setLoading(true);

  try {
    const resp = await fetch(`${API_BASE}/v1/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });

    const data = await resp.json();

    if (!resp.ok) {
      showError(data.error || "Invalid credentials. Please try again.");
      return;
    }

    localStorage.setItem("finomate_token", data.token);
    window.location.replace("/aws-connect.html");
  } catch (err) {
    showError("Network error — could not reach the server. Please try again.");
    console.error(err);
  } finally {
    setLoading(false);
  }
});
