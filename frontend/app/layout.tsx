import type { Metadata } from 'next';
import './globals.css';
export const metadata:Metadata={title:'DataPulse — Data Engineering & SAP Basis Jobs in Germany and India',
 description:'Find verified Data Engineering roles requiring up to 2 years and SAP Basis roles requiring up to 5 years in Germany and India. Working student, internship, junior, and full-time vacancies.'};
export default function RootLayout({children}:{children:React.ReactNode}){
 return <html lang="en"><body>{children}</body></html>;
}
