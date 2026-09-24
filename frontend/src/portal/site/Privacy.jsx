import { usePortalInfo } from '../hooks'

// A plain-language description of how the service actually handles data.
export default function Privacy() {
  const { name, contact_email: contact } = usePortalInfo()
  const sections = [
    ['What we store', `Your name, company, email address and a secure hash of your password (never the password itself). Inside your workspace, ${name} stores the leads, research, messages and settings you create.`],
    ['Your private workspace', 'Every account gets its own workspace with its own database. Other accounts can’t see your data, and your workspace can’t reach other accounts’ data.'],
    ['Email', `We email you only what the service needs: codes to confirm your email or reset your password, and messages about your account. Outreach emails are sent from the email account you connect yourself, only after you approve them.`],
    ['Research data', 'Lead research uses publicly available information — business listings, search results and company websites. Each detail is saved with where it was found; nothing is invented.'],
    ['People who opt out', 'If someone asks not to be contacted, they are marked as “do not contact” and are never contacted again.'],
    ['Deleting your data', `Ask us and we will delete your workspace and account.${contact ? ` Contact: ${contact}.` : ''}`],
  ]
  return (
    <section className="max-w-3xl mx-auto px-4 sm:px-6 py-16 sm:py-20">
      <p className="text-sm font-semibold text-primary">Privacy</p>
      <h1 className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight text-foreground">How {name} handles your data</h1>
      <p className="mt-4 text-lg text-muted-foreground leading-relaxed">A short, plain-language summary.</p>
      <div className="mt-12 space-y-10">
        {sections.map(([title, text]) => (
          <div key={title}>
            <h2 className="text-lg font-semibold text-foreground">{title}</h2>
            <p className="mt-2 text-[15px] text-muted-foreground leading-relaxed">{text}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
