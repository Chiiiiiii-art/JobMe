/**
 * JobMe Chrome Extension - Content Script
 * Extracts structured job details from supported Taiwanese job platforms.
 */

function cleanText(text) {
  if (!text) return "";
  return text
    .split("\n")
    .map(line => line.trim())
    .filter(Boolean)
    .join("\n");
}

function cleanTitle(raw) {
  if (!raw) return "";
  let t = raw.replace(/\s*[-－]\s*104.*$/i, "");
  t = t.replace(/\s*[|｜-]\s*(104|Cake|Yourator|1111|yes123).*$/i, "");
  if (t.includes("｜") || t.includes("|")) {
    t = t.split(/\s*[|｜]\s*/)[0];
  }
  return t.trim();
}

function cleanCompany(raw) {
  if (!raw) return "";
  let c = raw.split(/[\s|｜]+/)[0].trim();
  if (c.includes("推薦") || c.includes("好公司") || c.includes("為你推薦")) {
    return "";
  }
  return c.trim();
}

function checkIsJobPage(url, hostname) {
  if (hostname.includes("104.com.tw")) {
    return /\/job\/[a-zA-Z0-9]+/i.test(url);
  }
  if (hostname.includes("cake.me") || hostname.includes("cakeresume.com")) {
    return /\/companies\/[^/]+\/jobs\/[^/?#]+/i.test(url);
  }
  if (hostname.includes("yourator.co")) {
    return /\/(?:companies\/[^/]+\/)?jobs\/\d+/i.test(url);
  }
  if (hostname.includes("1111.com.tw")) {
    return /\/job\/\d+/i.test(url);
  }
  if (hostname.includes("yes123.com.tw")) {
    return /job_id=[a-zA-Z0-9_]+/i.test(url);
  }
  return false;
}

function extractJobData() {
  const url = window.location.href;
  const hostname = window.location.hostname;

  // 1. Strict validation: Is this a single job detail page?
  const isJob = checkIsJobPage(url, hostname);
  if (!isJob) {
    return {
      isJobPage: false,
      url,
      reason: "目前頁面非支援求職平台之單一職缺詳情頁 (僅支援 104、Cake、Yourator、1111、yes123 職缺內容頁)。",
    };
  }

  let platform = "unknown";
  let title = "";
  let company = "";
  let location = "";
  let salary = "";
  let description = "";

  if (hostname.includes("104.com.tw")) {
    platform = "104";

    // 104 Title from DOM
    const titleElem = document.querySelector(".job-header h1, h1.job-header__title, .job-header__title, h1");
    if (titleElem && titleElem.innerText.trim()) {
      title = cleanTitle(titleElem.innerText);
    }

    // 104 Company: Strict search inside header, avoiding recommendation sidebars
    const headerComp = document.querySelector(".job-header__company a, a[data-gtm-head='公司名稱'], .job-header a[href*='/company/']");
    if (headerComp && headerComp.innerText.trim()) {
      company = cleanCompany(headerComp.innerText);
    } else {
      const allCompLinks = Array.from(document.querySelectorAll("a[href*='/company/']"));
      const valid = allCompLinks.find(a => {
        const txt = a.innerText.trim();
        return txt && !txt.includes("推薦") && !txt.includes("好公司") && !a.closest(".recommend, aside, footer, .sidebar");
      });
      if (valid) {
        company = cleanCompany(valid.innerText);
      }
    }

    // Location from DOM
    const locElem = document.querySelector(".job-address, [data-gtm-head='工作地點'], .job-header__sub a[href*='maps'], .job-address-detail, [class*='address']");
    if (locElem && locElem.innerText.trim()) {
      location = locElem.innerText.replace(/[\s\n]+/g, " ").replace(/(看地圖|地圖)/g, "").trim();
    }

    // Salary from DOM
    const salElem = document.querySelector(".job-header__salary, [data-gtm-head='薪資待遇'], .job-header__sub .text-primary, [class*='salary']");
    if (salElem && salElem.innerText.trim()) {
      salary = salElem.innerText.replace(/[\s\n]+/g, " ").trim();
    }

    // Document title smart fallback for 104:
    // Format: "JobTitle｜CompanyName｜Location－104 人力銀行"
    if (document.title) {
      const rawTitle = document.title.replace(/\s*[-－]\s*104.*$/i, "").trim();
      const parts = rawTitle.split(/\s*[|｜]\s*/);
      if (parts.length >= 2) {
        if (!title || title.includes("｜") || title.includes("|")) {
          title = cleanTitle(parts[0]);
        }
        if (!company || company === "未知企業") {
          company = cleanCompany(parts[1]);
        }
        if (!location && parts.length >= 3) {
          location = parts[2].trim();
        }
      } else if (!title) {
        title = cleanTitle(rawTitle);
      }
    }

    const descElem = document.querySelector(".job-description__content, .job-detail-content, .job-description");
    description = descElem ? cleanText(descElem.innerText) : "";

  } else if (hostname.includes("cake.me") || hostname.includes("cakeresume.com")) {
    platform = "cake";
    const titleElem = document.querySelector("[class*='JobHeader_title'], h1");
    title = cleanTitle(titleElem?.innerText || "");

    const compElem = document.querySelector("[class*='JobHeader_companyName'] a, [class*='JobHeader'] a[href*='/companies/'], a[href*='/companies/']");
    company = cleanCompany(compElem?.innerText || "");

    const locElem = document.querySelector("[class*='JobHeader_location'], [class*='location_'], [class*='JobHeader'] i.fa-map-marker-alt + span, [class*='JobHeader'] [class*='location']");
    location = locElem?.innerText?.trim() || "";

    const salElem = document.querySelector("[class*='JobHeader_salary'], [class*='salary_'], [class*='JobHeader'] i.fa-dollar-sign + span, [class*='JobHeader'] [class*='salary']");
    salary = salElem?.innerText?.trim() || "";

    if (!company && document.title) {
      const parts = document.title.split(/\s*[|｜-]\s*/);
      if (parts.length >= 2) {
        if (!title) title = cleanTitle(parts[0]);
        company = cleanCompany(parts[1]);
      }
    }

    const descElem = document.querySelector("[class*='JobDescription_content'], [class*='content_'], article");
    description = descElem ? cleanText(descElem.innerText) : "";

  } else if (hostname.includes("yourator.co")) {
    platform = "yourator";
    const titleElem = document.querySelector("h1.basic-info__title, h1");
    title = cleanTitle(titleElem?.innerText || "");

    const compElem = document.querySelector(".company-info__name a, .basic-info__company-name a, a[href*='/companies/']");
    company = cleanCompany(compElem?.innerText || "");

    const locElem = document.querySelector(".basic-info__address, [class*='basic-info__location'], .basic-info a[href*='google.com/maps']");
    location = locElem?.innerText?.trim() || "";

    const salElem = document.querySelector(".basic-info__salary, [class*='basic-info__salary']");
    salary = salElem?.innerText?.trim() || "";

    if (!company && document.title) {
      const parts = document.title.split(/\s*[|｜-]\s*/);
      if (parts.length >= 2) {
        if (!title) title = cleanTitle(parts[0]);
        company = cleanCompany(parts[1]);
      }
    }

    const descElem = document.querySelector(".job-description, .job-content, section.job-description");
    description = descElem ? cleanText(descElem.innerText) : "";

  } else if (hostname.includes("1111.com.tw")) {
    platform = "1111";
    const titleElem = document.querySelector("h1, .job_title");
    title = cleanTitle(titleElem?.innerText || "");

    const compElem = document.querySelector(".job_company_name a, .ui_card_company, a[href*='/corp/']");
    company = cleanCompany(compElem?.innerText || "");

    const locElem = document.querySelector(".job_address, .ui_card_address, [class*='address']");
    location = locElem?.innerText?.trim() || "";

    const salElem = document.querySelector(".job_salary, .ui_card_salary, [class*='salary']");
    salary = salElem?.innerText?.trim() || "";

    if (!company && document.title) {
      const parts = document.title.split(/\s*[|｜-]\s*/);
      if (parts.length >= 2) {
        if (!title) title = cleanTitle(parts[0]);
        company = cleanCompany(parts[1]);
      }
    }

    const descElem = document.querySelector(".job_description, .content, .description");
    description = descElem ? cleanText(descElem.innerText) : "";

  } else if (hostname.includes("yes123.com.tw")) {
    platform = "yes123";
    const titleElem = document.querySelector("h1, .job_title");
    title = cleanTitle(titleElem?.innerText || document.title);

    const compElem = document.querySelector(".comp_name a, .company_name, a[href*='comp_info.asp']");
    company = cleanCompany(compElem?.innerText || "");

    const locElem = document.querySelector("[class*='job_address'], .job_detail_box");
    if (locElem && locElem.innerText.trim()) {
      const m = locElem.innerText.match(/(?:上班地點|工作地點)[:：]\s*([^\n]+)/);
      if (m) location = m[1].trim();
    }

    const salElem = document.querySelector("[class*='job_salary'], .job_detail_box");
    if (salElem && salElem.innerText.trim()) {
      const m = salElem.innerText.match(/(?:薪資待遇|工作待遇)[:：]\s*([^\n]+)/);
      if (m) salary = m[1].trim();
    }

    const descElements = document.querySelectorAll(".job_explain, .job_content");
    if (descElements.length > 0) {
      description = Array.from(descElements).map(el => cleanText(el.innerText)).join("\n\n");
    }
  }

  // Double check minimum validity
  if (!title || (!description && document.body.innerText.length < 100)) {
    return {
      isJobPage: false,
      url,
      reason: "無法辨識為有效的職缺詳情頁面內容。",
    };
  }

  return {
    isJobPage: true,
    url,
    platform,
    title,
    company: company || "未知企業",
    location: location || "",
    salary: salary || "",
    description,
  };
}

// Listen for message from popup
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "extract_job_info") {
    try {
      const data = extractJobData();
      sendResponse({ success: true, data });
    } catch (err) {
      sendResponse({ success: false, error: err.toString() });
    }
  }
  return true;
});
