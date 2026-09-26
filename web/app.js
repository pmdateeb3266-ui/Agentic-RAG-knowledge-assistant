const $ = id => document.getElementById(id);
let currentPage = 1;
let totalPages = 1;
const reviewState = {page: 1, totalPages: 1, selectedId: null, record: null};
const nativeFetch = window.fetch.bind(window);
let csrfToken = '';
let signedInUser = null;
let setupMode = false;

window.fetch = async (resource, options = {}) => {
  const method = String(options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken);
  }
  const response = await nativeFetch(resource, {...options, headers});
  if (response.status === 401 && !String(resource).startsWith('/api/auth/')) {
    showAuthScreen(false, 'Your session ended. Sign in again.');
  }
  return response;
};

function showAuthScreen(needsSetup = false, message = '') {
  setupMode = needsSetup;
  signedInUser = null;
  csrfToken = '';
  $('appMain').classList.add('hidden');
  $('userBar').classList.add('hidden');
  $('authScreen').classList.remove('hidden');
  $('authTitle').textContent = needsSetup ? 'Create administrator' : 'Sign in';
  $('authHelp').textContent = needsSetup
    ? 'This one-time account will control documents, maintenance, users, and backups.'
    : 'Use your administrator or reviewer account.';
  $('displayNameLabel').classList.toggle('hidden', !needsSetup);
  $('authDisplayName').required = needsSetup;
  $('authPassword').autocomplete = needsSetup ? 'new-password' : 'current-password';
  $('authSubmit').textContent = needsSetup ? 'Create administrator' : 'Sign in';
  $('authError').textContent = message;
  $('authUsername').focus();
}

function applyRoleVisibility() {
  const isAdmin = signedInUser?.role === 'admin';
  document.querySelectorAll('[data-admin-only]').forEach(element => {
    element.classList.toggle('hidden', !isAdmin);
  });
  $('userBadge').textContent = `${signedInUser.display_name} · ${isAdmin ? 'Administrator' : 'Reviewer'}`;
}

async function enterApplication(data) {
  signedInUser = data.user;
  csrfToken = data.csrf_token || '';
  $('authScreen').classList.add('hidden');
  $('appMain').classList.remove('hidden');
  $('userBar').classList.remove('hidden');
  applyRoleVisibility();
  await Promise.all([loadDocuments(), loadQuality()]);
  if (signedInUser.role === 'admin') await Promise.all([loadBackups(), loadUsers(), loadSecurityAudit()]);
}

async function initializeAccess() {
  try {
    const response = await nativeFetch('/api/auth/status');
    const data = await response.json();
    if (data.authenticated) await enterApplication(data);
    else showAuthScreen(Boolean(data.setup_required));
  } catch (error) {
    showAuthScreen(false, 'The application server is unavailable.');
  }
}

async function submitAuthentication(event) {
  event.preventDefault();
  $('authSubmit').disabled = true;
  $('authError').textContent = '';
  const payload = {
    username: $('authUsername').value.trim(),
    password: $('authPassword').value,
  };
  if (setupMode) payload.display_name = $('authDisplayName').value.trim();
  try {
    const response = await nativeFetch(setupMode ? '/api/auth/setup' : '/api/auth/login', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Sign-in failed');
    $('authPassword').value = '';
    await enterApplication(data);
    toast(setupMode ? 'Administrator created' : 'Signed in');
  } catch (error) {
    $('authError').textContent = error.message;
  } finally {
    $('authSubmit').disabled = false;
  }
}

async function signOut() {
  try { await fetch('/api/auth/logout', {method: 'POST'}); } catch (_) {}
  showAuthScreen(false);
}

async function changeMyPassword() {
  const currentPassword = prompt('Enter your current password:');
  if (currentPassword === null) return;
  const newPassword = prompt('Enter a new password of at least 10 characters:');
  if (newPassword === null) return;
  const confirmation = prompt('Type the new password again:');
  if (confirmation !== newPassword) return toast('The new passwords do not match');
  const response = await fetch('/api/auth/change-password', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({current_password: currentPassword, new_password: newPassword})
  });
  const data = await response.json();
  if (!response.ok) return toast(data.detail || 'Password could not be changed');
  showAuthScreen(false, 'Password changed. Sign in again.');
}

function userMarkup(user) {
  const state = user.enabled ? 'Active' : 'Disabled';
  return `<div class="user-item" data-user-id="${user.id}"><div class="user-item-head"><strong>${esc(user.display_name)}</strong><em>${esc(user.role)}</em></div><span>@${esc(user.username)} · ${state}</span><div class="user-item-actions"><button type="button" class="quiet toggle-user" data-enabled="${user.enabled ? 'false' : 'true'}">${user.enabled ? 'Disable' : 'Enable'}</button><button type="button" class="quiet reset-user">Reset password</button></div></div>`;
}

async function loadUsers() {
  if (signedInUser?.role !== 'admin') return;
  const response = await fetch('/api/admin/users');
  const data = await response.json();
  if (!response.ok) return;
  $('userList').innerHTML = (data.users || []).map(userMarkup).join('');
}

async function createAccount(event) {
  event.preventDefault();
  $('userState').textContent = 'Creating account…';
  const body = {
    display_name: $('newDisplayName').value.trim(), username: $('newUsername').value.trim(),
    password: $('newPassword').value, role: $('newRole').value,
  };
  const response = await fetch('/api/admin/users', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) return $('userState').textContent = data.detail || 'Account could not be created';
  $('newUserForm').reset();
  $('userState').textContent = `${data.user.display_name} can now sign in.`;
  await Promise.all([loadUsers(), loadSecurityAudit()]);
}

async function changeUserState(userId, enabled) {
  const response = await fetch(`/api/admin/users/${userId}/enabled`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled})
  });
  const data = await response.json();
  if (!response.ok) return toast(data.detail || 'User could not be changed');
  toast(enabled ? 'User enabled' : 'User disabled');
  await Promise.all([loadUsers(), loadSecurityAudit()]);
}

async function resetUserPassword(userId) {
  const password = prompt('Enter a new password of at least 10 characters:');
  if (password === null) return;
  const response = await fetch(`/api/admin/users/${userId}/password`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({password})
  });
  const data = await response.json();
  if (!response.ok) return toast(data.detail || 'Password could not be changed');
  toast('Password changed; that user must sign in again');
  await loadSecurityAudit();
}

async function loadSecurityAudit() {
  if (signedInUser?.role !== 'admin') return;
  const response = await fetch('/api/admin/audit?limit=30');
  const data = await response.json();
  if (!response.ok) return;
  $('securityAudit').innerHTML = (data.events || []).map(event => `<div class="audit-item"><strong>${esc(event.username || 'System')} · ${esc(event.action)}</strong><span>${esc(event.created_at)} · ${event.success ? 'Succeeded' : 'Failed'}</span></div>`).join('') || '<p class="muted">No security activity yet.</p>';
}

async function timedFetch(url, options = {}, milliseconds = 120000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), milliseconds);
  try {
    return await fetch(url, {...options, signal: controller.signal});
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The search timed out. Try a more specific field or document.');
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

function toast(text) {
  $('toast').textContent = text;
  $('toast').classList.remove('hidden');
  setTimeout(() => $('toast').classList.add('hidden'), 4000);
}

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));

async function loadDocuments() {
  const selected = $('document').value;
  const response = await fetch('/api/documents');
  const data = await response.json();
  $('document').innerHTML = '<option value="all">All indexed documents</option>';
  for (const indexedDocument of data.documents) {
    const option = document.createElement('option');
    option.value = indexedDocument.source;
    const expected = Number(indexedDocument.expected_records || 0);
    const missing = indexedDocument.missing_records;
    option.textContent = expected
      ? `${indexedDocument.source} · ${indexedDocument.record_count}/${expected}${missing ? ` · ${missing} missing` : ' · complete'}`
      : `${indexedDocument.source} · ${indexedDocument.record_count} records · not audited`;
    $('document').appendChild(option);
  }
  if ([...$('document').options].some(option => option.value === selected)) {
    $('document').value = selected;
  }
  const totals = data.totals || {};
  if (totals.audited_documents === data.documents.length && data.documents.length) {
    $('documentMeta').textContent = `${data.documents.length} documents · ${totals.indexed}/${totals.expected} records · ${totals.missing} missing`;
  } else {
    $('documentMeta').textContent = `${data.documents.length} documents · ${totals.indexed || 0} indexed records · integrity audit pending`;
  }
}

async function loadQuality() {
  try {
    const response = await fetch('/api/quality');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Quality check failed');
    $('qualityMeta').textContent = `Quality snapshot: ${data.clear_records} without actionable warnings · ${data.review_records} need OCR review across ${data.review_pages} pages · ${data.manually_verified_records || 0} manually verified · ${data.notice_records || 0} source notes`;
    const warnings = Object.entries(data.warning_categories || {});
    const notices = Object.entries(data.notice_categories || {});
    $('qualityBreakdown').innerHTML = (warnings.length || notices.length)
      ? `<details class="quality-details"><summary>View quality categories</summary>${warnings.length ? `<strong>OCR review</strong><ul>${warnings.map(([label, count]) => `<li>${esc(label)}: ${esc(count)}</li>`).join('')}</ul>` : ''}${notices.length ? `<strong>Source notes</strong><ul>${notices.map(([label, count]) => `<li>${esc(label)}: ${esc(count)}</li>`).join('')}</ul>` : ''}</details>`
      : '';
  } catch (error) {
    $('qualityMeta').textContent = 'Quality status unavailable';
    $('qualityBreakdown').innerHTML = '';
  }
}

async function loadBackups(selectedFilename = '') {
  try {
    const response = await fetch('/api/backups');
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Backup list unavailable');
    const backups = data.backups || [];
    const select = $('backupSelect');
    const previous = selectedFilename || select.value;
    select.innerHTML = '<option value="">Select a backup</option>';
    for (const backup of backups) {
      const option = document.createElement('option');
      option.value = backup.filename;
      const date = backup.created_at ? new Date(backup.created_at).toLocaleString() : 'Unknown date';
      const counts = backup.database_counts || {};
      option.textContent = backup.valid
        ? `${date} · ${counts.records ?? 0} records`
        : `${backup.filename} · invalid`;
      option.disabled = !backup.valid;
      select.appendChild(option);
    }
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
    $('backupMeta').textContent = backups.length
      ? `${backups.length} verified backup${backups.length === 1 ? '' : 's'} available`
      : 'No backups yet';
    if (data.restore_pending) {
      $('backupState').textContent = 'A verified restore is staged. Restart the server to apply it.';
    }
  } catch (error) {
    $('backupMeta').textContent = 'Backup status unavailable';
    $('backupState').textContent = error.message;
  }
}

async function createDataBackup() {
  const button = $('createBackup');
  button.disabled = true;
  $('backupState').textContent = 'Creating and verifying a complete data snapshot…';
  $('backupLink').innerHTML = '';
  try {
    const response = await fetch('/api/backups', {method: 'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Backup failed');
    $('backupState').textContent = `Backup verified · ${data.database_counts.records} records · ${data.database_counts.manually_verified} manually verified · ${data.database_counts.users ?? 0} user accounts`;
    $('backupLink').innerHTML = `<a class="backup-download" href="${esc(data.download_url)}">Download ${esc(data.filename)}</a>`;
    await loadBackups(data.filename);
    toast('Verified backup created');
  } catch (error) {
    $('backupState').textContent = error.message;
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function verifySelectedBackup() {
  const filename = $('backupSelect').value;
  if (!filename) return toast('Select a backup first');
  $('backupState').textContent = 'Checking every backup file and checksum…';
  try {
    const response = await fetch(`/api/backups/${encodeURIComponent(filename)}/verify`, {method: 'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Backup verification failed');
    $('backupState').textContent = `Verified · ${data.database_counts.documents} documents · ${data.database_counts.records} records · ${data.database_counts.users ?? 0} user accounts · ${data.file_count} data files`;
    $('backupLink').innerHTML = `<a class="backup-download" href="/api/backups/${encodeURIComponent(filename)}/download">Download selected backup</a>`;
    toast('Backup is valid');
  } catch (error) {
    $('backupState').textContent = error.message;
    toast(error.message);
  }
}

async function restoreSelectedBackup() {
  const filename = $('backupSelect').value;
  if (!filename) return toast('Select a backup first');
  if (!confirm('Restoring will replace the current database and embeddings after the server restarts. A safety backup will be created first. Continue?')) return;
  const confirmation = prompt('Type RESTORE exactly to confirm:');
  if (confirmation !== 'RESTORE') return toast('Restore cancelled');
  $('restoreBackup').disabled = true;
  $('backupState').textContent = 'Verifying backup and creating a safety snapshot…';
  try {
    const response = await fetch(`/api/backups/${encodeURIComponent(filename)}/restore`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({confirmation})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Restore could not be staged');
    $('backupState').textContent = 'Restore is verified and staged. Press Ctrl+C in PowerShell, then run python run_web.py again.';
    toast('Restore staged safely; restart the server');
    await loadBackups(filename);
  } catch (error) {
    $('backupState').textContent = error.message;
    toast(error.message);
  } finally {
    $('restoreBackup').disabled = false;
  }
}

function payload() {
  return {
    question: $('question').value.trim(),
    language: $('language').value,
    document: $('document').value,
    page: currentPage,
  };
}

function renderTrust(trust = {}) {
  const labels = {
    sqlite_structured: 'Verified database',
    sqlite_exact: 'Verified exact lookup',
    chroma_semantic: 'Semantic evidence',
  };
  $('answerPath').textContent = labels[trust.path] || 'Evidence check';
  $('answerConfidence').textContent = trust.confidence === 'deterministic'
    ? 'Deterministic'
    : `${String(trust.confidence || 'insufficient').replace(/^./, value => value.toUpperCase())} confidence`;
  $('answerConfidence').className = `trust-badge confidence-${trust.confidence || 'insufficient'}`;
  const citations = trust.citations || [];
  $('citations').innerHTML = citations.length
    ? citations.map(item => `<span class="citation">${esc(item.source)}${item.page ? ` · page ${esc(item.page)}` : ''}</span>`).join('')
    : '<span class="citation muted-inline">No supporting page citation</span>';
}

function renderRows(results, trust) {
  $('rows').innerHTML = '';
  const structured = (trust?.path || '').startsWith('sqlite');
  $('recordsTitle').textContent = structured ? 'Records' : 'Retrieved evidence';
  $('recordHead').classList.toggle('hidden', !structured);
  $('evidenceHead').classList.toggle('hidden', structured);

  for (const item of results) {
    const row = document.createElement('tr');
    if (!structured) {
      const metadata = item.metadata || {};
      const confidence = item.retrieval_confidence || 'low';
      const score = item.relevance_score == null ? '' : `${Math.round(item.relevance_score * 100)}%`;
      row.innerHTML = `<td>${esc(metadata.source)}</td><td>${esc(metadata.page)}</td><td>${esc(item.scope)}</td><td><span class="confidence-text confidence-${esc(confidence)}">${esc(confidence)} ${esc(score)}</span></td><td class="evidence-text">${esc(item.snippet || item.document)}</td>`;
      $('rows').appendChild(row);
      continue;
    }
    const record = item.record || {};
    const quality = item.quality_status === 'human_verified'
      ? '<span class="human-verified">Human verified</span>'
      : item.quality_warnings?.length
      ? `<span class="warning" title="${esc(item.quality_warnings.join('; '))}">Review</span>`
      : item.quality_notices?.length
        ? `<span class="notice" title="${esc(item.quality_notices.join('; '))}">Source note</span>`
        : '<span class="ok">Clear</span>';
    row.innerHTML = `<td>${esc(record.source_document)}</td><td>${esc(record.page)}</td><td>${esc(record.serial_number)}</td><td>${esc(record.house_number)}</td><td>${esc(record.voter_name)}</td><td>${esc(record.gender_normalized)}</td><td>${esc(record.age)}</td><td>${esc(record.epic_id)}</td><td>${quality}</td>`;
    $('rows').appendChild(row);
  }
}

async function ask() {
  if (!$('question').value.trim()) return toast('Enter a question first');
  $('ask').disabled = true;
  $('ask').textContent = 'Searching…';
  try {
    const response = await timedFetch('/api/query', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload())
    });
    const data = await response.json();
    if (!response.ok) throw Error(data.detail || 'Query failed');
    $('answer').textContent = data.answer;
    renderTrust(data.trust);
    $('answerCard').classList.remove('hidden');
    const facts = data.facts || {};
    const semantic = data.trust?.path === 'chroma_semantic';
    const count = semantic ? (data.trust?.evidence_count ?? data.results.length) : (facts.matched_records ?? data.results.length);
    $('matchCount').textContent = semantic ? `${count} evidence items` : `${count} matches`;
    totalPages = facts.pagination?.total_pages || 1;
    $('pageLabel').textContent = `Page ${currentPage} of ${totalPages}`;
    $('prev').disabled = currentPage <= 1;
    $('next').disabled = currentPage >= totalPages;
    renderRows(data.results, data.trust);
    $('resultsCard').classList.toggle('hidden', !data.results.length);
  } catch (error) {
    toast(error.message);
  } finally {
    $('ask').disabled = false;
    $('ask').textContent = 'Ask question';
  }
}

async function exportCsv() {
  if (!$('question').value.trim()) return toast('Enter a structured question first');
  const response = await fetch('/api/export', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...payload(), show_all: true})
  });
  const data = await response.json();
  if (!response.ok) return toast(data.detail || 'Export failed');
  location.href = data.download_url;
  toast(`${data.records} records exported`);
}

async function upload() {
  const file = $('file').files[0];
  if (!file) return toast('Choose a PDF first');
  const form = new FormData();
  form.append('file', file);
  const response = await fetch('/api/documents/upload', {method: 'POST', body: form});
  const data = await response.json();
  if (!response.ok) return toast(data.detail || 'Upload failed');
  $('uploadState').textContent = 'Uploaded. Preparing index…';
  $('jobProgress').classList.remove('hidden');
  pollJob(data.job_id, $('uploadState'), $('jobProgress'), () => {
    toast('Indexing complete');
    location.reload();
  });
}

async function pollJob(id, stateElement, progress, onComplete) {
  const response = await fetch(`/api/jobs/${id}`);
  const data = await response.json();
  stateElement.textContent = data.message || data.status;
  if (Number.isFinite(data.progress_percent)) progress.value = data.progress_percent;
  else progress.removeAttribute('value');
  if (data.status === 'running' || data.status === 'queued') {
    setTimeout(() => pollJob(id, stateElement, progress, onComplete), 2000);
  } else {
    progress.classList.add('hidden');
    if (data.status === 'complete') {
      if (data.download_url) {
        $('reportLink').innerHTML = `<a class="report-link" href="${esc(data.download_url)}">Open accuracy report</a>`;
      }
      if (onComplete) onComplete(data);
    } else toast(data.message || 'Indexing failed');
  }
}

async function startManagedJob(url, button, startingText, completeText) {
  button.disabled = true;
  $('trustJobState').textContent = startingText;
  $('trustJobProgress').classList.remove('hidden');
  $('reportLink').innerHTML = '';
  try {
    const response = await fetch(url, {method: 'POST'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'The job could not be started');
    pollJob(data.job_id, $('trustJobState'), $('trustJobProgress'), () => {
      button.disabled = false;
      toast(completeText);
      loadQuality();
      loadDocuments();
    });
  } catch (error) {
    button.disabled = false;
    $('trustJobProgress').classList.add('hidden');
    $('trustJobState').textContent = error.message;
    toast(error.message);
  }
}

function reviewParams() {
  const params = new URLSearchParams({
    source: $('reviewSource').value,
    warning: $('reviewWarning').value,
    status: $('reviewStatus').value,
    page: reviewState.page,
    page_size: 20,
  });
  const documentPage = $('reviewDocumentPage').value.trim();
  if (documentPage) params.set('document_page', documentPage);
  return params;
}

function updateReviewSelect(id, values, allLabel) {
  const select = $(id);
  const current = select.value;
  select.innerHTML = `<option value="all">${esc(allLabel)}</option>`;
  for (const value of values || []) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = value;
    select.appendChild(option);
  }
  if ([...select.options].some(option => option.value === current)) select.value = current;
}

function queueRecordMarkup(record) {
  const active = record.id === reviewState.selectedId ? ' active' : '';
  const warningText = (record.warnings || []).map(item => `<span>${esc(item.replace(/\s*\([^)]*\)$/, ''))}</span>`).join('');
  const status = record.verification_status === 'verified' ? 'Verified' : 'Needs review';
  return `<button type="button" class="queue-item${active}" data-record-id="${record.id}"><span class="queue-line"><strong>${esc(record.source)}</strong><em>${esc(status)}</em></span><span>Page ${esc(record.page)} · serial ${esc(record.fields.serial_number || 'unknown')} · house ${esc(record.fields.house_number || 'unknown')}</span><span class="queue-name">${esc(record.fields.voter_name || 'Name unreadable')}</span><span class="warning-chips">${warningText}</span></button>`;
}

async function loadReviewQueue(selectFirst = false) {
  $('reviewQueue').innerHTML = '<div class="queue-message">Loading review records…</div>';
  try {
    const response = await fetch(`/api/review/records?${reviewParams()}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Review records could not be loaded');
    reviewState.page = data.page;
    reviewState.totalPages = data.total_pages;
    updateReviewSelect('reviewSource', data.filters?.sources, 'All documents');
    updateReviewSelect('reviewWarning', data.filters?.warnings, 'All warnings');
    $('reviewCount').textContent = `${data.total} ${data.total === 1 ? 'record' : 'records'}`;
    $('reviewPageLabel').textContent = `Page ${data.page} of ${data.total_pages}`;
    $('reviewPrev').disabled = data.page <= 1;
    $('reviewNext').disabled = data.page >= data.total_pages;
    $('reviewQueue').innerHTML = data.records.length
      ? data.records.map(queueRecordMarkup).join('')
      : '<div class="queue-message"><strong>No records in this view.</strong><span>Try another document, warning, page, or status.</span></div>';
    const selectedStillVisible = data.records.some(record => record.id === reviewState.selectedId);
    if ((selectFirst || !selectedStillVisible) && data.records.length) {
      await loadReviewRecord(data.records[0].id);
    } else if (!data.records.length) {
      clearReviewEditor();
    } else {
      $('reviewQueue').innerHTML = data.records.map(queueRecordMarkup).join('');
    }
  } catch (error) {
    $('reviewQueue').innerHTML = `<div class="queue-message"><strong>Review list unavailable</strong><span>${esc(error.message)}</span></div>`;
  }
}

function clearReviewEditor() {
  reviewState.selectedId = null;
  reviewState.record = null;
  $('reviewEmpty').classList.remove('hidden');
  $('reviewFormWrap').classList.add('hidden');
  $('reviewEditor').classList.add('empty-editor');
}

function historyMarkup(history) {
  if (!history.length) return '<p class="muted">No manual changes have been saved for this record.</p>';
  const latestActive = history.find(item => item.action !== 'rollback' && !item.reverted_at);
  return history.map(item => {
    const label = item.action === 'rollback' ? `Rollback of correction #${item.reversed_correction_id}` : item.action === 'verify' ? 'Verified without field changes' : `Updated ${item.changed_fields.join(', ')}`;
    const state = item.reverted_at ? '<span class="history-reverted">Rolled back</span>' : '';
    const rollback = latestActive?.id === item.id ? `<button type="button" class="quiet rollback-review" data-correction-id="${item.id}">Roll back</button>` : '';
    const actor = item.actor || item.reverted_by || 'Earlier local user';
    return `<div class="history-item"><div><strong>${esc(label)}</strong>${state}<span>${esc(item.created_at)} · by ${esc(actor)}${item.note ? ` · ${esc(item.note)}` : ''}</span></div>${rollback}</div>`;
  }).join('');
}

function renderReviewRecord(record, history) {
  reviewState.selectedId = record.id;
  reviewState.record = record;
  $('reviewEmpty').classList.add('hidden');
  $('reviewFormWrap').classList.remove('hidden');
  $('reviewEditor').classList.remove('empty-editor');
  $('reviewRecordStatus').textContent = record.verification_status === 'verified' ? 'Human verified' : 'Needs review';
  $('reviewRecordStatus').className = `pill ${record.verification_status === 'verified' ? 'verified-pill' : 'review-pill'}`;
  $('reviewRecordTitle').textContent = `${record.source} · page ${record.page} · serial ${record.fields.serial_number || 'unknown'}`;
  $('reviewConfidence').textContent = `OCR confidence ${Math.round(record.ocr_confidence)}%`;
  $('reviewWarnings').innerHTML = record.warnings?.length
    ? `<strong>${record.verification_status === 'verified' ? 'Originally flagged; checked by a person' : 'Check these fields'}</strong><ul>${record.warnings.map(item => `<li>${esc(item)}</li>`).join('')}</ul>`
    : '<strong>No unresolved automatic warnings</strong>';
  for (const field of Object.keys(record.fields)) {
    const input = $('reviewForm').elements.namedItem(field);
    if (input) input.value = record.fields[field] || '';
  }
  $('reviewForm').elements.namedItem('note').value = record.verification_note || '';
  $('reviewSaveState').textContent = '';
  $('reviewImageError').classList.add('hidden');
  $('reviewImage').classList.remove('hidden');
  $('reviewImage').src = `${record.image_url}?revision=${record.record_revision}`;
  $('reviewImage').alt = `Original row for serial ${record.fields.serial_number || 'unknown'} on page ${record.page} of ${record.source}`;
  $('reviewHistory').innerHTML = historyMarkup(history || []);
  loadReviewQueueSelection();
}

function loadReviewQueueSelection() {
  document.querySelectorAll('.queue-item').forEach(item => {
    item.classList.toggle('active', Number(item.dataset.recordId) === reviewState.selectedId);
  });
}

async function loadReviewRecord(recordId) {
  try {
    const response = await fetch(`/api/review/records/${recordId}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Review record could not be opened');
    renderReviewRecord(data.record, data.history);
  } catch (error) {
    toast(error.message);
  }
}

function reviewFormFields() {
  const form = $('reviewForm');
  const fields = {};
  for (const name of ['serial_number', 'house_number', 'voter_name', 'relationship', 'related_name', 'gender', 'age', 'epic_id']) {
    fields[name] = form.elements.namedItem(name).value;
  }
  return fields;
}

async function saveReview(includeEdits) {
  const record = reviewState.record;
  if (!record) return;
  const body = includeEdits ? reviewFormFields() : {};
  body.note = $('reviewForm').elements.namedItem('note').value;
  body.expected_revision = record.record_revision;
  $('saveReview').disabled = true;
  $('verifyShown').disabled = true;
  $('reviewSaveState').textContent = 'Saving and refreshing search indexes…';
  try {
    const response = await timedFetch(`/api/review/records/${record.id}`, {
      method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
    }, 300000);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'The verification could not be saved');
    renderReviewRecord(data.record, data.history);
    toast('Record verified and search indexes updated');
    await Promise.all([loadQuality(), loadReviewQueue(false)]);
  } catch (error) {
    $('reviewSaveState').textContent = error.message;
    toast(error.message);
  } finally {
    $('saveReview').disabled = false;
    $('verifyShown').disabled = false;
  }
}

async function rollbackReview(correctionId) {
  if (!reviewState.record) return;
  $('reviewSaveState').textContent = 'Restoring the previous values…';
  try {
    const response = await timedFetch(`/api/review/corrections/${correctionId}/rollback`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expected_revision: reviewState.record.record_revision})
    }, 300000);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'The correction could not be rolled back');
    renderReviewRecord(data.record, data.history);
    toast('Correction rolled back and search indexes updated');
    await Promise.all([loadQuality(), loadReviewQueue(false)]);
  } catch (error) {
    $('reviewSaveState').textContent = error.message;
    toast(error.message);
  }
}

$('ask').onclick = () => { currentPage = 1; ask(); };
$('export').onclick = exportCsv;
$('upload').onclick = upload;
$('recoverRecords').onclick = () => {
  const selected = $('document').value;
  const suffix = selected && selected !== 'all' ? `?source=${encodeURIComponent(selected)}` : '';
  startManagedJob(`/api/integrity/recover${suffix}`, $('recoverRecords'), 'Auditing serial numbers and locating missing records…', 'Missing-record recovery complete');
};
$('reviewOcr').onclick = () => startManagedJob('/api/quality/reprocess', $('reviewOcr'), 'Preparing targeted OCR review…', 'OCR review complete');
$('runAccuracy').onclick = () => startManagedJob('/api/accuracy/run', $('runAccuracy'), 'Preparing accuracy checks…', 'Accuracy report complete');
$('createBackup').onclick = createDataBackup;
$('verifyBackup').onclick = verifySelectedBackup;
$('restoreBackup').onclick = restoreSelectedBackup;
$('openReview').onclick = () => {
  $('reviewWorkspace').classList.remove('hidden');
  reviewState.page = 1;
  loadReviewQueue(true);
  $('reviewWorkspace').scrollIntoView({behavior: 'smooth', block: 'start'});
};
$('closeReview').onclick = () => $('reviewWorkspace').classList.add('hidden');
$('refreshReview').onclick = () => loadReviewQueue(false);
$('reviewPrev').onclick = () => { if (reviewState.page > 1) { reviewState.page--; loadReviewQueue(true); } };
$('reviewNext').onclick = () => { if (reviewState.page < reviewState.totalPages) { reviewState.page++; loadReviewQueue(true); } };
for (const id of ['reviewSource', 'reviewWarning', 'reviewStatus', 'reviewDocumentPage']) {
  $(id).addEventListener('change', () => { reviewState.page = 1; loadReviewQueue(true); });
}
$('reviewQueue').addEventListener('click', event => {
  const item = event.target.closest('[data-record-id]');
  if (item) loadReviewRecord(Number(item.dataset.recordId));
});
$('reviewForm').addEventListener('submit', event => { event.preventDefault(); saveReview(true); });
$('verifyShown').onclick = () => saveReview(false);
$('reviewHistory').addEventListener('click', event => {
  const button = event.target.closest('[data-correction-id]');
  if (button) rollbackReview(Number(button.dataset.correctionId));
});
$('reviewImage').addEventListener('error', () => {
  $('reviewImage').classList.add('hidden');
  $('reviewImageError').classList.remove('hidden');
});
$('authForm').addEventListener('submit', submitAuthentication);
$('logout').onclick = signOut;
$('changePassword').onclick = changeMyPassword;
$('newUserForm').addEventListener('submit', createAccount);
$('userList').addEventListener('click', event => {
  const item = event.target.closest('[data-user-id]');
  if (!item) return;
  const userId = Number(item.dataset.userId);
  if (event.target.closest('.toggle-user')) changeUserState(userId, event.target.closest('.toggle-user').dataset.enabled === 'true');
  if (event.target.closest('.reset-user')) resetUserPassword(userId);
});
$('prev').onclick = () => { if (currentPage > 1) { currentPage--; ask(); } };
$('next').onclick = () => { if (currentPage < totalPages) { currentPage++; ask(); } };
$('question').addEventListener('keydown', event => { if (event.ctrlKey && event.key === 'Enter') { currentPage = 1; ask(); } });
fetch('/api/health').then(response => response.json()).then(data => {
  $('status').textContent = `System ready · v${data.version}`;
}).catch(() => { $('status').textContent = 'System unavailable'; });
initializeAccess();
