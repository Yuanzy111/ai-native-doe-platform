import { Navigate, Route, Routes } from 'react-router-dom'
import CampaignDemoV2 from './pages/campaigns/demo-v2/CampaignDemoV2'

function App() {
  return (
    <Routes>
      <Route path="/campaigns/demo-v2" element={<CampaignDemoV2 />} />
      <Route path="/" element={<Navigate to="/campaigns/demo-v2" replace />} />
    </Routes>
  )
}

export default App
