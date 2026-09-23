import { Link } from 'react-router-dom'
import { ChevronLeft } from 'lucide-react'

// Detail pages (quick search, deep research, campaign, automation) all start
// from the Find leads page — this is the way back.
export default function BackToFindLeads() {
  return (
    <Link to="/lead-search"
      className="mb-3 -ml-1 inline-flex items-center gap-0.5 text-sm text-muted-foreground hover:text-foreground transition-colors">
      <ChevronLeft size={16} /> Find leads
    </Link>
  )
}
