import React, { useState, useEffect } from 'react'
import axios from 'axios'

function LogPanel() {
  const [logs, setLogs] = useState([])

  const fetchLogs = async () => {
    try {
      const res = await axios.get('/api/logs')
      setLogs(res.data)
    } catch (e) {
      console.error("Failed to fetch logs", e)
    }
  }

  useEffect(() => {
    fetchLogs()
    const interval = setInterval(fetchLogs, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div style={{ marginTop: '20px', padding: '15px', background: '#333', color: '#fff', borderRadius: '5px', maxHeight: '300px', overflowY: 'auto' }}>
      <h3>Interaction Logs</h3>
      {logs.map((log, i) => (
        <div key={i} style={{ marginBottom: '10px', fontSize: '0.9em' }}>
          <span style={{ color: '#aaa' }}>[{log.timestamp}]</span> <strong>{log.type}:</strong> {log.content}
        </div>
      ))}
    </div>
  )
}

export default LogPanel
