import { app, type BrowserWindow } from 'electron'
import updaterModule from 'electron-updater'

const { autoUpdater } = updaterModule

interface SetupUpdaterOptions {
  getMainWindow: () => BrowserWindow | null
  getUpdateWindow: () => BrowserWindow | null
}

/** In-memory state shared with the update window renderer. */
let currentVersion: string | null = null
let isDownloaded = false

export const setupUpdater = ({ getMainWindow, getUpdateWindow }: SetupUpdaterOptions) => {
  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = true

  const customFeedUrl = process.env.VALUZ_UPDATER_URL
  if (customFeedUrl) {
    autoUpdater.setFeedURL({
      provider: 'generic',
      url: customFeedUrl,
    })
  }

  const sendToRenderer = (event: string, payload?: unknown) => {
    const mainWindow = getMainWindow()
    if (!mainWindow) {
      return
    }

    mainWindow.webContents.send(event, payload)
  }

  const sendToAll = (event: string, payload?: unknown) => {
    sendToRenderer(event, payload)
    const uw = getUpdateWindow()
    if (uw && !uw.isDestroyed()) {
      uw.webContents.send(event, payload)
    }
  }

  autoUpdater.on('checking-for-update', () => {
    sendToRenderer('updater:checking')
  })

  autoUpdater.on('update-available', (info) => {
    currentVersion = info.version ?? null
    sendToRenderer('updater:available', info)
  })

  autoUpdater.on('update-not-available', (info) => {
    sendToRenderer('updater:not-available', info)
  })

  autoUpdater.on('download-progress', (progress) => {
    sendToAll('updater:progress', progress)
  })

  autoUpdater.on('update-downloaded', (info) => {
    isDownloaded = true
    sendToAll('updater:downloaded', info)
  })

  autoUpdater.on('error', (error) => {
    sendToAll('updater:error', { message: error.message })
  })

  const isDev = !app.isPackaged

  const checkForUpdates = async () => {
    if (isDev) {
      sendToRenderer('updater:not-available', { reason: 'development-mode' })
      return
    }

    await autoUpdater.checkForUpdates()
  }

  const downloadUpdate = async () => {
    if (isDev) {
      for (let i = 0; i <= 100; i += 2) {
        await new Promise(r => setTimeout(r, 80))
        sendToAll('updater:progress', { percent: i, bytesPerSecond: 2_500_000 })
      }
      isDownloaded = true
      sendToAll('updater:downloaded', { version: currentVersion })
      return
    }
    await autoUpdater.downloadUpdate()
  }

  const quitAndInstall = () => {
    autoUpdater.quitAndInstall()
  }

  const getUpdaterState = () => ({
    version: currentVersion,
    status: isDownloaded ? 'downloaded' as const : 'available' as const,
    progress: 0,
    bytesPerSecond: 0,
  })

  return {
    checkForUpdates,
    downloadUpdate,
    quitAndInstall,
    getUpdaterState,
  }
}

export const scheduleUpdateCheck = async (checkForUpdates: () => Promise<void>) => {
  if (!app.isPackaged) {
    return
  }

  await checkForUpdates()
  setInterval(() => {
    void checkForUpdates()
  }, 30 * 60 * 1000)
}
