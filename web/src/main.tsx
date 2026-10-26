import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import App from './App'
import './styles/tokens.css'
import './styles/app.css'
// Round 4: one layout file per workstream, loaded after the base sheet so a
// screen can restyle its own blocks without touching app.css.
import './styles/shell.css'
import './styles/inbox.css'
import './styles/agents.css'
import './styles/integrations.css'
import './styles/record.css'

const root = document.getElementById('root')
if (!root) throw new Error('missing #root')

createRoot(root).render(
  <StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </StrictMode>,
)
