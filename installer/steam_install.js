async function(request) {
  // Use the running Steam client's own install manager, not a separate downloader.
  const allowed = [438100, 1493710, 1826330, 3127680, 4183110];
  if (!allowed.includes(request.appid) || !['start', 'read', 'continue', 'resume', 'remove'].includes(request.action) ||
      (request.action === 'remove' && request.appid !== 438100))
    throw Error('Unexpected dependency operation');
  const api = SteamClient.Installs;
  if (typeof api?.OpenInstallWizard !== 'function' || typeof api?.ContinueInstall !== 'function' ||
      typeof api?.GetInstallManagerInfo !== 'function')
    throw Error('Steam installation API changed');
  function snapshot(info) {
    if (!info) return {phase: 'unknown'};
    if (info.eInstallState === 0) return {phase: 'idle'};
    if (!Number.isInteger(info.eInstallState)) return {phase: 'unknown'};
    const ids = (info.rgApps || []).map(app => app.nAppID);
    const permitted = request.appid === 438100 ? allowed : allowed.filter(id => id !== 438100);
    if (ids.some(id => !permitted.includes(id)) ||
        (info.currentAppID && !permitted.includes(info.currentAppID))) return {phase: 'foreign'};
    if (info.eInstallState === 16) return {phase: 'canceled'};
    if (info.eInstallState === 15) return {phase: 'failed'};
    if (info.eInstallState === 14) return {phase: 'complete'};
    if (info.eInstallState === 7) {
      if (!ids.includes(request.appid) || new Set(ids).size !== ids.length ||
          !Number.isFinite(info.nDiskSpaceAvailable) || !Number.isFinite(info.nDiskSpaceRequired) ||
          info.nDiskSpaceRequired < 0 || info.nDiskSpaceAvailable <= info.nDiskSpaceRequired ||
          info.iUnmountedFolder !== -1) return {phase: 'confirmation'};
      return {phase: 'config'};
    }
    if ([4, 6, 8, 11, 13].includes(info.eInstallState)) return {phase: 'confirmation'};
    if ([1, 2, 3, 5, 9, 10, 12].includes(info.eInstallState)) return {phase: 'working'};
    return {phase: 'unknown'};
  }
  const state = snapshot(await api.GetInstallManagerInfo());
  if (request.action === 'remove') {
    if (!['idle','complete'].includes(state.phase) || typeof api.OpenUninstallWizard !== 'function')
      throw Error('別のSteam操作が進行中、または削除APIを確認できません。削除せず停止します。');
    // The Windows GUI or explicit CLI flag has already confirmed data-loss consent.
    await api.OpenUninstallWizard([438100], '0', true);
    return {phase: 'removing'};
  }
  if (request.action === 'start') {
    if (!['idle','complete'].includes(state.phase)) throw Error('別のSteamインストール画面が開いています。完了してから再実行してください。');
    await api.OpenInstallWizard([request.appid], '0');
    return {phase: 'opened'};
  }
  if (request.action === 'continue') {
    if (state.phase !== 'config') throw Error('Steam install state changed; no confirmation was accepted');
    await api.ContinueInstall();
    return {phase: 'continued'};
  }
  if (request.action === 'resume') {
    if (request.appid !== 438100 || state.phase !== 'idle' ||
        typeof SteamClient.Downloads?.ResumeAppUpdate !== 'function')
      throw Error('VRChat download is not ready to resume');
    await SteamClient.Downloads.ResumeAppUpdate(438100, '0');
    return {phase: 'resumed'};
  }
  return state;
}
