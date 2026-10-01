import fs from 'fs'
import CryptoJS from 'crypto-js'

const token = process.env.PAT_TOKEN
const password = process.env.DEV_PANEL_PASSWORD
const envFile = process.env.GITHUB_ENV

if (!token || !password) {
  console.warn('⚠️ PAT_TOKEN or DEV_PANEL_PASSWORD not set. Using empty string.')
  if (envFile) fs.appendFileSync(envFile, `VITE_ENCRYPTED_PAT=\n`)
  process.exit(0)
}

// Encrypt the token using AES
const ciphertext = CryptoJS.AES.encrypt(token, password).toString()

// Write to GITHUB_ENV so the next step (Vite build) receives it directly
if (envFile) {
  fs.appendFileSync(envFile, `VITE_ENCRYPTED_PAT=${ciphertext}\n`)
  console.log('✅ Token successfully encrypted and saved to GITHUB_ENV.')
} else {
  console.warn('⚠️ GITHUB_ENV not found (running locally?).')
}

