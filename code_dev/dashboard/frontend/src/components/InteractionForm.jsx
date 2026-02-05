import React, { useState, useEffect } from 'react'
import axios from 'axios'

function InteractionForm() {
  const [waiting, setWaiting] = useState({ questions: [] })
  const [answers, setAnswers] = useState({})

  const fetchWaiting = async () => {
    try {
      const res = await axios.get('/api/waiting-for-input')
      setWaiting(res.data)
    } catch (e) {
      console.error("Failed to fetch waiting status", e)
    }
  }

  useEffect(() => {
    fetchWaiting()
    const interval = setInterval(fetchWaiting, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    try {
      await axios.post('/api/user-input', { answers })
      setAnswers({})
      setWaiting({ questions: [] })
      alert("Answers sent!")
    } catch (e) {
      alert("Failed to send answers")
    }
  }

  if (!waiting.questions || waiting.questions.length === 0) return null

  return (
    <div style={{ marginTop: '20px', padding: '15px', background: '#fff3cd', border: '1px solid #ffeeba', borderRadius: '5px' }}>
      <h3>User Input Required</h3>
      <form onSubmit={handleSubmit}>
        {waiting.questions.map((q, i) => (
          <div key={i} style={{ marginBottom: '10px' }}>
            <label style={{ display: 'block', marginBottom: '5px' }}>{q}</label>
            <input
              type="text"
              style={{ width: '100%', padding: '8px' }}
              onChange={(e) => setAnswers({ ...answers, [q]: e.target.value })}
              required
            />
          </div>
        ))}
        <button type="submit" style={{ padding: '8px 15px', background: '#856404', color: '#fff', border: 'none', borderRadius: '4px' }}>
          Submit Answers
        </button>
      </form>
    </div>
  )
}

export default InteractionForm
