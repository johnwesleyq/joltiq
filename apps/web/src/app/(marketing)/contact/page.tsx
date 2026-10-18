export const metadata = { title: "Contact" };

export default function Contact() {
  const email = process.env.NEXT_PUBLIC_SUPPORT_EMAIL || "support@joltiq.io";
  return <main className="mx-auto max-w-[720px] px-6 py-10">
    <h1 className="text-4xl font-bold">Contact</h1>
    <p className="mt-3 text-lg text-slate">For sales, security questionnaires or support, email us and a person will reply within one business day.</p>
    <p className="mt-6"><a className="btn btn-primary" href={`mailto:${email}?subject=JoltIQ%20enquiry`}>Email {email}</a></p>
  </main>;
}
