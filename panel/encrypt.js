import fs from 'fs'
import CryptoJS from 'crypto-js'

// This script runs during GitHub Actions build.
// It takes the secret token and password, encrypts the token,
// and outputs it to a .env file so Vite can bundle the ciphertext.

const token = process.env.PAT_TOKEN
const password = process.env.DEV_PANEL_PASSWORD

if (!token || !password) {
  console.warn('⚠️ PAT_TOKEN or DEV_PANEL_PASSWORD not set. Using empty string.')
  fs.writeFileSync('.env', `VITE_ENCRYPTED_PAT=\n`)
  process.exit(0)
}

// Encrypt the token using AES
const ciphertext = CryptoJS.AES.encrypt(token, password).toString()

// Write to .env for Vite
fs.writeFileSync('.env', `VITE_ENCRYPTED_PAT=${ciphertext}\n`)
console.log('✅ Token successfully encrypted for frontend.')
