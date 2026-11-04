'use strict';
document.getElementById('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button');
  const error = document.getElementById('login-error');
  button.disabled = true;
  button.textContent = 'Signing in…';
  error.textContent = '';
  try {
    const response = await fetch('/api/login', { method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: document.getElementById('username').value, password: document.getElementById('password').value}) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Could not sign in.');
    window.location.assign('/');
  } catch (err) {
    error.textContent = err.message || 'Could not sign in.';
    button.disabled = false;
    button.textContent = 'Sign in';
  }
});
