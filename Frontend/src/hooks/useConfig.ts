import { useEffect, useState } from "react";

import { getConfig } from "../api/chat";
import type { PublicConfig } from "../api/types";

const FALLBACK: PublicConfig = {
  company_name: "",
  hr_contact_email: "",
  feedback_enabled: false,
  admin_enabled: false,
  max_upload_mb: 10,
  attachments_enabled: false,
  max_attachment_mb: 10,
  max_attachments_per_message: 5,
};

/** Company branding and HR contact details served by the backend. */
export function useConfig(): PublicConfig {
  const [config, setConfig] = useState<PublicConfig>(FALLBACK);

  useEffect(() => {
    let cancelled = false;
    getConfig()
      .then((c) => !cancelled && setConfig(c))
      .catch(() => {
        // Non-critical: the chat still works without branding, feedback or the HR link.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return config;
}
