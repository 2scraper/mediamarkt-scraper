#!/usr/bin/env python3
"""
MediaMarkt Scraper - Playwright Edition
========================================
High-performance web scraper for MediaMarkt product data.
Supports all categories, anti-detection, captcha solving, and proxy rotation.

GitHub: https://github.com/2scraper/mediamarkt-scraper
Captcha Solving: https://2captcha.com
Proxy Service: https://2prx.com

License: MIT
"""

import asyncio
import json
import csv
import random
import logging
import os
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict, field
from urllib.parse import urljoin, urlparse, parse_qs

try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext
except ImportError:
    raise ImportError("Please install playwright: pip install playwright && playwright install")

try:
    from twocaptcha import TwoCaptcha
except ImportError:
    TwoCaptcha = None
    print("Warning: 2captcha not installed. Run: pip install 2captcha-python")

# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class ScraperConfig:
    """Scraper configuration settings."""
    
    # 2captcha.com API key for solving captchas
    captcha_api_key: str = ""
    
    # 2prx.com proxy settings
    proxy_host: str = ""
    proxy_port: int = 0
    proxy_username: str = ""
    proxy_password: str = ""
    
    # Output settings
    output_format: str = "json"  # json or csv
    output_dir: str = "./output"
    
    # Scraping settings
    headless: bool = True
    timeout: int = 30000
    max_retries: int = 3
    delay_min: float = 1.0
    delay_max: float = 3.0
    
    # Anti-detection settings
    use_fingerprint_spoofing: bool = True
    use_stealth_mode: bool = True
    
    # Categories to scrape (empty = all)
    categories: List[str] = field(default_factory=list)
    
    # Pagination
    max_pages_per_category: int = 0  # 0 = unlimited
    
    # Debug mode - saves HTML for troubleshooting
    debug: bool = False
    debug_dir: str = "./debug"
    
    @classmethod
    def from_env(cls) -> "ScraperConfig":
        """Load configuration from environment variables."""
        return cls(
            captcha_api_key=os.getenv("TWOCAPTCHA_API_KEY", ""),
            proxy_host=os.getenv("PROXY_HOST", ""),
            proxy_port=int(os.getenv("PROXY_PORT", "0")),
            proxy_username=os.getenv("PROXY_USER", ""),
            proxy_password=os.getenv("PROXY_PASS", ""),
            output_format=os.getenv("OUTPUT_FORMAT", "json"),
            output_dir=os.getenv("OUTPUT_DIR", "./output"),
            headless=os.getenv("HEADLESS", "true").lower() == "true",
        )


@dataclass
class Product:
    """Product data structure."""
    
    id: str = ""
    name: str = ""
    brand: str = ""
    price: float = 0.0
    original_price: float = 0.0
    currency: str = "EUR"
    availability: str = ""
    rating: float = 0.0
    reviews_count: int = 0
    url: str = ""
    image_url: str = ""
    category: str = ""
    subcategory: str = ""
    description: str = ""
    specifications: Dict[str, str] = field(default_factory=dict)
    ean: str = ""
    sku: str = ""
    scraped_at: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        data['specifications'] = json.dumps(self.specifications) if self.specifications else ""
        return data


# ============================================================================
# FINGERPRINT SPOOFING
# ============================================================================

class FingerprintGenerator:
    """Generate realistic browser fingerprints for anti-detection."""
    
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    ]
    
    VIEWPORTS = [
        {"width": 1920, "height": 1080},
        {"width": 1366, "height": 768},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
        {"width": 1280, "height": 720},
        {"width": 2560, "height": 1440},
    ]
    
    LOCALES = ["de-DE", "de-AT", "de-CH", "en-DE"]
    TIMEZONES = ["Europe/Berlin", "Europe/Vienna", "Europe/Zurich"]
    
    WEBGL_VENDORS = [
        "Google Inc. (NVIDIA)",
        "Google Inc. (Intel)",
        "Google Inc. (AMD)",
    ]
    
    WEBGL_RENDERERS = [
        "ANGLE (NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0)",
        "ANGLE (Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
        "ANGLE (AMD Radeon RX 6800 XT Direct3D11 vs_5_0 ps_5_0)",
        "ANGLE (NVIDIA GeForce GTX 1660 Direct3D11 vs_5_0 ps_5_0)",
    ]
    
    @classmethod
    def generate(cls) -> Dict[str, Any]:
        """Generate a random fingerprint."""
        viewport = random.choice(cls.VIEWPORTS)
        return {
            "user_agent": random.choice(cls.USER_AGENTS),
            "viewport": viewport,
            "locale": random.choice(cls.LOCALES),
            "timezone_id": random.choice(cls.TIMEZONES),
            "device_scale_factor": random.choice([1, 1.25, 1.5, 2]),
            "has_touch": False,
            "is_mobile": False,
            "color_scheme": random.choice(["light", "dark"]),
            "webgl_vendor": random.choice(cls.WEBGL_VENDORS),
            "webgl_renderer": random.choice(cls.WEBGL_RENDERERS),
            "screen": {
                "width": viewport["width"],
                "height": viewport["height"],
                "availWidth": viewport["width"],
                "availHeight": viewport["height"] - 40,
                "colorDepth": 24,
                "pixelDepth": 24,
            },
            "hardware_concurrency": random.choice([4, 8, 12, 16]),
            "device_memory": random.choice([4, 8, 16, 32]),
            "platform": "Win32",
            "plugins_length": random.randint(3, 7),
            "languages": ["de-DE", "de", "en-US", "en"],
        }


# ============================================================================
# STEALTH MODE INJECTION
# ============================================================================

STEALTH_JS = """
// Override webdriver detection
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});

// Override plugins
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
            { name: 'Native Client', filename: 'internal-nacl-plugin' },
        ];
        plugins.item = (index) => plugins[index];
        plugins.namedItem = (name) => plugins.find(p => p.name === name);
        plugins.refresh = () => {};
        return plugins;
    }
});

// Override languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['de-DE', 'de', 'en-US', 'en']
});

// Override permissions query
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
    Promise.resolve({ state: Notification.permission }) :
    originalQuery(parameters)
);

// Override chrome detection
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};

// Prevent automation detection via CDP
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

// Override WebGL
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Google Inc. (NVIDIA)';
    if (parameter === 37446) return 'ANGLE (NVIDIA GeForce RTX 3080 Direct3D11 vs_5_0 ps_5_0)';
    return getParameter.call(this, parameter);
};

// Console.log protection
const originalConsoleLog = console.log;
console.log = function(...args) {
    if (args.some(arg => String(arg).includes('detect'))) return;
    originalConsoleLog.apply(console, args);
};
"""


# ============================================================================
# CAPTCHA SOLVER
# ============================================================================

class CaptchaSolver:
    """2captcha.com integration for solving captchas."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.solver = None
        if TwoCaptcha and api_key:
            self.solver = TwoCaptcha(api_key)
    
    async def solve_recaptcha_v2(self, sitekey: str, page_url: str) -> Optional[str]:
        """Solve reCAPTCHA v2."""
        if not self.solver:
            logging.warning("2captcha not configured. Cannot solve captcha.")
            return None
        
        try:
            logging.info(f"Solving reCAPTCHA v2 for {page_url}")
            result = await asyncio.to_thread(
                self.solver.recaptcha,
                sitekey=sitekey,
                url=page_url
            )
            logging.info("reCAPTCHA v2 solved successfully")
            return result['code']
        except Exception as e:
            logging.error(f"Failed to solve reCAPTCHA v2: {e}")
            return None
    
    async def solve_recaptcha_v3(self, sitekey: str, page_url: str, action: str = "verify") -> Optional[str]:
        """Solve reCAPTCHA v3."""
        if not self.solver:
            logging.warning("2captcha not configured. Cannot solve captcha.")
            return None
        
        try:
            logging.info(f"Solving reCAPTCHA v3 for {page_url}")
            result = await asyncio.to_thread(
                self.solver.recaptcha,
                sitekey=sitekey,
                url=page_url,
                version='v3',
                action=action,
                score=0.9
            )
            logging.info("reCAPTCHA v3 solved successfully")
            return result['code']
        except Exception as e:
            logging.error(f"Failed to solve reCAPTCHA v3: {e}")
            return None
    
    async def solve_hcaptcha(self, sitekey: str, page_url: str) -> Optional[str]:
        """Solve hCaptcha."""
        if not self.solver:
            logging.warning("2captcha not configured. Cannot solve captcha.")
            return None
        
        try:
            logging.info(f"Solving hCaptcha for {page_url}")
            result = await asyncio.to_thread(
                self.solver.hcaptcha,
                sitekey=sitekey,
                url=page_url
            )
            logging.info("hCaptcha solved successfully")
            return result['code']
        except Exception as e:
            logging.error(f"Failed to solve hCaptcha: {e}")
            return None
    
    async def solve_turnstile(self, sitekey: str, page_url: str) -> Optional[str]:
        """Solve Cloudflare Turnstile."""
        if not self.solver:
            logging.warning("2captcha not configured. Cannot solve captcha.")
            return None
        
        try:
            logging.info(f"Solving Turnstile for {page_url}")
            result = await asyncio.to_thread(
                self.solver.turnstile,
                sitekey=sitekey,
                url=page_url
            )
            logging.info("Turnstile solved successfully")
            return result['code']
        except Exception as e:
            logging.error(f"Failed to solve Turnstile: {e}")
            return None


# ============================================================================
# MAIN SCRAPER CLASS
# ============================================================================

class MediaMarktScraper:
    """
    MediaMarkt web scraper using Playwright.
    
    Features:
    - All categories scraping with auto-discovery
    - Anti-detection with fingerprint spoofing
    - Captcha solving via 2captcha.com
    - Proxy support via 2prx.com
    - JSON/CSV export
    - Debug mode for troubleshooting
    """
    
    BASE_URL = "https://www.mediamarkt.de"
    
    # MediaMarkt category URLs (Updated April 2026)
    # These are entry points - the scraper will discover products automatically
    CATEGORIES = {
        "tv-audio": "/de/category/tv-audio-519.html",
        "computing": "/de/category/computer-buero-458.html",
        "smartphones": "/de/category/telefon-navigation-440.html",
        "gaming": "/de/category/gaming-pc-konsolen-546.html",
        "photo-video": "/de/category/foto-camcorder-407.html",
        "household": "/de/category/haushalt-725.html",
        "kitchen": "/de/category/kuechenwelt-758.html",
        "personal-care": "/de/category/koerperpflege-770.html",
        "smart-home": "/de/category/smart-home-802.html",
        "wearables": "/de/category/wearables-smartwatches-808.html",
        "car-tech": "/de/category/e-mobilitaet-815.html",
        "office": "/de/category/buero-826.html",
        "diy-garden": "/de/category/heimwerken-garten-831.html",
        "toys": "/de/category/spielzeug-842.html",
        "music-instruments": "/de/category/musikinstrumente-843.html",
        "movies-music": "/de/category/filme-musik-679.html",
        "accessories": "/de/category/zubehoer-850.html",
    }
    
    # Multiple product card selector strategies
    PRODUCT_SELECTORS = [
        '[data-test="mms-product-card"]',
        '[data-testid="mms-product-card"]',
        'div[class*="ProductCard"]',
        'article[class*="product"]',
        'div[class*="product-card"]',
        'a[href*="/de/product/"]',
        '[class*="StyledProductCard"]',
        'li[class*="product"]',
    ]
    
    # Name selectors
    NAME_SELECTORS = [
        '[data-test="product-title"]',
        '[data-testid="product-title"]',
        'p[class*="Typo"][class*="title"]',
        'h2[class*="title"]',
        'span[class*="ProductTitle"]',
        '.product-title',
        'h2', 'h3',
    ]
    
    # Price selectors
    PRICE_SELECTORS = [
        '[data-test="product-price"]',
        '[data-testid="product-price"]',
        'span[class*="Price"]',
        '[class*="price"]',
        'span[class*="Typo"][class*="price"]',
    ]
    
    def __init__(self, config: ScraperConfig):
        self.config = config
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.products: List[Product] = []
        self.captcha_solver = CaptchaSolver(config.captcha_api_key)
        self.fingerprint = FingerprintGenerator.generate()
        
        # Setup logging
        log_level = logging.DEBUG if config.debug else logging.INFO
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Create output directory
        os.makedirs(config.output_dir, exist_ok=True)
        
        # Create debug directory if debug mode is enabled
        if config.debug:
            os.makedirs(config.debug_dir, exist_ok=True)
            self.logger.info(f"Debug mode enabled. HTML saved to {config.debug_dir}")
    
    def _get_proxy_config(self) -> Optional[Dict[str, Any]]:
        """Build proxy configuration for 2prx.com."""
        if not self.config.proxy_host:
            return None
        
        proxy = {
            "server": f"http://{self.config.proxy_host}:{self.config.proxy_port}"
        }
        
        if self.config.proxy_username:
            proxy["username"] = self.config.proxy_username
            proxy["password"] = self.config.proxy_password
        
        return proxy
    
    async def _setup_browser(self):
        """Initialize browser with anti-detection settings."""
        playwright = await async_playwright().start()
        
        launch_options = {
            "headless": self.config.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-size=1920,1080",
                "--start-maximized",
            ]
        }
        
        # Add proxy if configured
        proxy = self._get_proxy_config()
        if proxy:
            launch_options["proxy"] = proxy
            self.logger.info(f"Using proxy: {self.config.proxy_host}:{self.config.proxy_port}")
        
        self.browser = await playwright.chromium.launch(**launch_options)
        
        # Create context with fingerprint
        context_options = {
            "user_agent": self.fingerprint["user_agent"],
            "viewport": self.fingerprint["viewport"],
            "locale": self.fingerprint["locale"],
            "timezone_id": self.fingerprint["timezone_id"],
            "device_scale_factor": self.fingerprint["device_scale_factor"],
            "has_touch": self.fingerprint["has_touch"],
            "is_mobile": self.fingerprint["is_mobile"],
            "color_scheme": self.fingerprint["color_scheme"],
        }
        
        self.context = await self.browser.new_context(**context_options)
        
        # Apply stealth mode
        if self.config.use_stealth_mode:
            await self.context.add_init_script(STEALTH_JS)
        
        self.page = await self.context.new_page()
        self.page.set_default_timeout(self.config.timeout)
        
        self.logger.info("Browser initialized with anti-detection settings")
    
    async def _random_delay(self):
        """Add random delay to mimic human behavior."""
        delay = random.uniform(self.config.delay_min, self.config.delay_max)
        await asyncio.sleep(delay)
    
    async def _detect_and_solve_captcha(self) -> bool:
        """Detect and solve captcha if present (reCAPTCHA, hCaptcha, or Cloudflare Turnstile)."""
        try:
            # Check page title for Cloudflare challenge
            title = await self.page.title()
            content = await self.page.content()
            
            # Check for Cloudflare Turnstile
            if "Nur einen Moment" in title or "Just a moment" in title or "challenges.cloudflare.com/turnstile" in content:
                self.logger.info("Cloudflare Turnstile detected!")
                
                # Try to find turnstile sitekey
                sitekey = None
                
                # Method 1: From turnstile widget
                turnstile_widget = await self.page.query_selector('[class*="cf-turnstile"]')
                if turnstile_widget:
                    sitekey = await turnstile_widget.get_attribute('data-sitekey')
                
                # Method 2: Extract from script in page
                if not sitekey:
                    sitekey_match = re.search(r'sitekey["\']?\s*[=:]\s*["\']([^"\']+)["\']', content)
                    if sitekey_match:
                        sitekey = sitekey_match.group(1)
                
                # Method 3: Common MediaMarkt Turnstile sitekey
                if not sitekey:
                    # Try known Turnstile sitekeys for MediaMarkt
                    sitekey = "0x4AAAAAAADQ_tLiTFhG9-7K"  # Common pattern
                
                if sitekey and self.captcha_solver.solver:
                    self.logger.info(f"Attempting to solve Turnstile with sitekey: {sitekey[:20]}...")
                    token = await self.captcha_solver.solve_turnstile(sitekey, self.page.url)
                    
                    if token:
                        # Inject the token
                        await self.page.evaluate(f'''
                            const responses = document.querySelectorAll('[name="cf-turnstile-response"], [id*="cf-chl-widget"]');
                            responses.forEach(el => el.value = "{token}");
                            
                            // Try to submit
                            const form = document.querySelector('form');
                            if (form) form.submit();
                        ''')
                        
                        # Wait for redirect
                        await asyncio.sleep(3)
                        
                        # Check if we passed
                        new_title = await self.page.title()
                        if "Nur einen Moment" not in new_title and "Just a moment" not in new_title:
                            self.logger.info("Cloudflare Turnstile solved successfully!")
                            return True
                        else:
                            self.logger.warning("Turnstile solution may have failed")
                else:
                    self.logger.warning("Cloudflare Turnstile detected but no 2captcha API key provided!")
                    self.logger.info("To bypass Turnstile, use: --captcha-key YOUR_2CAPTCHA_API_KEY")
                    return False
            
            # Check for reCAPTCHA
            recaptcha = await self.page.query_selector('[data-sitekey]')
            if recaptcha:
                sitekey = await recaptcha.get_attribute('data-sitekey')
                if sitekey:
                    token = await self.captcha_solver.solve_recaptcha_v2(
                        sitekey, 
                        self.page.url
                    )
                    if token:
                        await self.page.evaluate(f'''
                            document.getElementById("g-recaptcha-response").innerHTML = "{token}";
                        ''')
                        await self.page.click('[type="submit"]')
                        await self._random_delay()
                        return True
            
            # Check for hCaptcha
            hcaptcha = await self.page.query_selector('[data-hcaptcha-sitekey]')
            if hcaptcha:
                sitekey = await hcaptcha.get_attribute('data-hcaptcha-sitekey')
                if sitekey:
                    token = await self.captcha_solver.solve_hcaptcha(sitekey, self.page.url)
                    if token:
                        await self.page.evaluate(f'''
                            document.querySelector("[name='h-captcha-response']").value = "{token}";
                        ''')
                        await self._random_delay()
                        return True
            
            return False
        except Exception as e:
            self.logger.error(f"Error handling captcha: {e}")
            return False
    
    async def _accept_cookies(self):
        """Accept cookie consent banner."""
        try:
            cookie_selectors = [
                '[data-test="pwa-consent-layer-accept-all"]',
                '#onetrust-accept-btn-handler',
                '[id*="accept"]',
                'button:has-text("Alle akzeptieren")',
                'button:has-text("Accept All")',
            ]
            
            for selector in cookie_selectors:
                try:
                    button = await self.page.wait_for_selector(selector, timeout=3000)
                    if button:
                        await button.click()
                        self.logger.info("Cookie consent accepted")
                        await self._random_delay()
                        return
                except:
                    continue
        except Exception as e:
            self.logger.debug(f"No cookie banner found: {e}")
    
    async def _extract_product_data(self, product_element) -> Optional[Product]:
        """Extract product data from a product card element."""
        try:
            product = Product()
            product.scraped_at = datetime.now().isoformat()
            
            # Product URL and ID - try multiple link patterns
            link = None
            for link_selector in ['a[href*="/de/product/"]', 'a[href*="/product/"]', 'a[href]']:
                link = await product_element.query_selector(link_selector)
                if link:
                    href = await link.get_attribute('href')
                    if href and '/product/' in href:
                        product.url = urljoin(self.BASE_URL, href)
                        # Extract product ID from URL patterns
                        match = re.search(r'-(\d{6,})\.html', href)
                        if match:
                            product.id = match.group(1)
                        break
            
            # Product name - try multiple selectors
            for name_selector in self.NAME_SELECTORS:
                name_el = await product_element.query_selector(name_selector)
                if name_el:
                    text = (await name_el.inner_text()).strip()
                    if text and len(text) > 3:
                        product.name = text
                        break
            
            # Brand (usually first word of name or separate element)
            brand_el = await product_element.query_selector('[data-test="product-brand"], [class*="brand"], .brand')
            if brand_el:
                product.brand = (await brand_el.inner_text()).strip()
            elif product.name:
                # Extract brand from name (usually first word)
                product.brand = product.name.split()[0] if product.name else ""
            
            # Price - try multiple selectors
            for price_selector in self.PRICE_SELECTORS:
                price_el = await product_element.query_selector(price_selector)
                if price_el:
                    price_text = await price_el.inner_text()
                    # Parse German price format (1.234,56 € or 1234,56€)
                    price_match = re.search(r'([\d.]+)[,.](\d{2})\s*[€$]?', price_text)
                    if price_match:
                        price_str = price_match.group(1).replace('.', '') + '.' + price_match.group(2)
                        try:
                            product.price = float(price_str)
                            break
                        except ValueError:
                            continue
            
            # Original price (if on sale)
            orig_price_el = await product_element.query_selector('[data-test="product-price-strike"], [class*="strikethrough"], [class*="original"], .price-old')
            if orig_price_el:
                orig_price_text = await orig_price_el.inner_text()
                orig_match = re.search(r'([\d.]+)[,.](\d{2})', orig_price_text)
                if orig_match:
                    orig_str = orig_match.group(1).replace('.', '') + '.' + orig_match.group(2)
                    try:
                        product.original_price = float(orig_str)
                    except ValueError:
                        pass
            
            # Rating
            rating_el = await product_element.query_selector('[data-test="product-rating"], [class*="rating"], [class*="stars"]')
            if rating_el:
                rating_text = await rating_el.get_attribute('aria-label') or await rating_el.get_attribute('title') or await rating_el.inner_text()
                rating_match = re.search(r'([\d,\.]+)', rating_text)
                if rating_match:
                    try:
                        product.rating = float(rating_match.group(1).replace(',', '.'))
                    except ValueError:
                        pass
            
            # Reviews count
            reviews_el = await product_element.query_selector('[data-test="product-reviews"], [class*="review"], .reviews-count')
            if reviews_el:
                reviews_text = await reviews_el.inner_text()
                reviews_match = re.search(r'(\d+)', reviews_text)
                if reviews_match:
                    product.reviews_count = int(reviews_match.group(1))
            
            # Availability
            avail_el = await product_element.query_selector('[data-test="product-availability"], [class*="availability"], [class*="stock"]')
            if avail_el:
                product.availability = (await avail_el.inner_text()).strip()
            
            # Image URL
            img_el = await product_element.query_selector('img')
            if img_el:
                product.image_url = (
                    await img_el.get_attribute('src') or 
                    await img_el.get_attribute('data-src') or
                    await img_el.get_attribute('data-lazy-src') or
                    ""
                )
            
            return product if product.name else None
            
        except Exception as e:
            self.logger.error(f"Error extracting product data: {e}")
            return None
    
    async def _scrape_product_details(self, product: Product) -> Product:
        """Scrape additional details from product page."""
        try:
            await self.page.goto(product.url, wait_until="domcontentloaded")
            await self._random_delay()
            
            # Check for captcha
            await self._detect_and_solve_captcha()
            
            # Description
            desc_el = await self.page.query_selector('[data-test="product-description"], .product-description, [itemprop="description"]')
            if desc_el:
                product.description = (await desc_el.inner_text()).strip()
            
            # Specifications
            spec_rows = await self.page.query_selector_all('[data-test="specification-row"], .spec-row, tr')
            for row in spec_rows[:20]:  # Limit to first 20 specs
                try:
                    label_el = await row.query_selector('th, .spec-label, dt')
                    value_el = await row.query_selector('td, .spec-value, dd')
                    if label_el and value_el:
                        label = (await label_el.inner_text()).strip()
                        value = (await value_el.inner_text()).strip()
                        if label and value:
                            product.specifications[label] = value
                except:
                    continue
            
            # EAN
            ean_el = await self.page.query_selector('[itemprop="gtin13"], [data-test="product-ean"]')
            if ean_el:
                product.ean = (await ean_el.get_attribute('content') or await ean_el.inner_text()).strip()
            
            # SKU
            sku_el = await self.page.query_selector('[itemprop="sku"], [data-test="product-sku"]')
            if sku_el:
                product.sku = (await sku_el.get_attribute('content') or await sku_el.inner_text()).strip()
            
        except Exception as e:
            self.logger.error(f"Error scraping product details for {product.url}: {e}")
        
        return product
    
    async def _save_debug_html(self, category_name: str, page_num: int):
        """Save HTML for debugging purposes."""
        if not self.config.debug:
            return
        
        try:
            html = await self.page.content()
            filename = f"{self.config.debug_dir}/{category_name}_page{page_num}.html"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(html)
            self.logger.debug(f"Saved debug HTML to {filename}")
        except Exception as e:
            self.logger.warning(f"Could not save debug HTML: {e}")
    
    async def _find_product_cards(self) -> list:
        """Try multiple selectors to find product cards."""
        for selector in self.PRODUCT_SELECTORS:
            try:
                cards = await self.page.query_selector_all(selector)
                if cards and len(cards) > 0:
                    self.logger.debug(f"Found {len(cards)} products with selector: {selector}")
                    return cards
            except Exception:
                continue
        
        # Fallback: try to find any links to product pages
        try:
            links = await self.page.query_selector_all('a[href*="/de/product/"], a[href*="/product/"]')
            if links:
                self.logger.debug(f"Found {len(links)} product links as fallback")
                return links
        except Exception:
            pass
        
        return []
    
    async def _scrape_category(self, category_name: str, category_url: str):
        """Scrape all products from a category."""
        self.logger.info(f"Scraping category: {category_name}")
        
        full_url = urljoin(self.BASE_URL, category_url)
        page_num = 1
        category_products = 0
        captcha_retries = 0
        max_captcha_retries = 3
        
        while True:
            # Check page limit
            if self.config.max_pages_per_category > 0 and page_num > self.config.max_pages_per_category:
                break
            
            # Navigate to page
            page_url = f"{full_url}?page={page_num}" if page_num > 1 else full_url
            self.logger.info(f"Scraping page {page_num}: {page_url}")
            
            try:
                # Try multiple navigation strategies
                try:
                    await self.page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                except Exception as nav_err:
                    self.logger.warning(f"Navigation timeout, retrying with load: {nav_err}")
                    try:
                        await self.page.goto(page_url, wait_until="load", timeout=45000)
                    except:
                        await self.page.goto(page_url, timeout=60000)
                
                await self._random_delay()
                
                # Check for Cloudflare or CAPTCHA and try to solve it
                captcha_solved = await self._detect_and_solve_captcha()
                
                if captcha_solved:
                    self.logger.info("CAPTCHA solved, waiting for page reload...")
                    await asyncio.sleep(3)
                    # Reload the page after solving CAPTCHA
                    try:
                        await self.page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
                        await self._random_delay()
                    except:
                        pass
                
                # Check if still on CAPTCHA page
                title = await self.page.title()
                if "Nur einen Moment" in title or "Just a moment" in title:
                    captcha_retries += 1
                    if captcha_retries >= max_captcha_retries:
                        self.logger.error(f"Failed to bypass Cloudflare after {max_captcha_retries} attempts")
                        self.logger.info("Try using: --captcha-key YOUR_2CAPTCHA_KEY or residential proxy from 2prx.com")
                        break
                    self.logger.warning(f"Still on CAPTCHA page, retry {captcha_retries}/{max_captcha_retries}")
                    await asyncio.sleep(5)
                    continue
                
                # Reset captcha retry counter on success
                captcha_retries = 0
                
                # Accept cookies on first page
                if page_num == 1:
                    await self._accept_cookies()
                
                # Wait for page to fully load
                await asyncio.sleep(2)
                
                # Save debug HTML if enabled
                await self._save_debug_html(category_name, page_num)
                
                # Try to find product cards with multiple selectors
                product_cards = await self._find_product_cards()
                
                if not product_cards:
                    self.logger.warning(f"No products found on page {page_num}. URL might be incorrect or site structure changed.")
                    if self.config.debug:
                        self.logger.info(f"Check debug HTML at {self.config.debug_dir}/{category_name}_page{page_num}.html")
                    break
                
                self.logger.info(f"Found {len(product_cards)} products on page {page_num}")
                
                # Extract products
                for card in product_cards:
                    product = await self._extract_product_data(card)
                    if product and product.name:
                        product.category = category_name
                        self.products.append(product)
                        category_products += 1
                
                # Check for next page
                next_selectors = [
                    '[data-test="pagination-next"]:not([disabled])',
                    '[data-testid="pagination-next"]:not([disabled])',
                    'a[rel="next"]',
                    'button[aria-label*="next" i]:not([disabled])',
                    'a[aria-label*="Nächste"]',
                    '[class*="pagination"] a:last-child',
                ]
                
                next_button = None
                for sel in next_selectors:
                    try:
                        next_button = await self.page.query_selector(sel)
                        if next_button:
                            break
                    except:
                        continue
                
                if not next_button:
                    self.logger.info(f"No more pages in category {category_name}")
                    break
                
                page_num += 1
                
            except Exception as e:
                self.logger.error(f"Error on page {page_num}: {e}")
                if self.config.debug:
                    await self._save_debug_html(category_name, page_num)
                if page_num == 1:
                    break
                continue
        
        self.logger.info(f"Completed category {category_name}: {category_products} products scraped")
    
    async def scrape(self, scrape_details: bool = False):
        """
        Main scraping method.
        
        Args:
            scrape_details: If True, visit each product page for additional details
        """
        self.logger.info("Starting MediaMarkt scraper")
        
        try:
            await self._setup_browser()
            
            # Determine categories to scrape
            categories = {}
            if self.config.categories:
                categories = {k: v for k, v in self.CATEGORIES.items() if k in self.config.categories}
            else:
                categories = self.CATEGORIES
            
            self.logger.info(f"Will scrape {len(categories)} categories")
            
            # Scrape each category
            for cat_name, cat_url in categories.items():
                await self._scrape_category(cat_name, cat_url)
                await self._random_delay()
            
            # Optionally scrape product details
            if scrape_details and self.products:
                self.logger.info(f"Scraping details for {len(self.products)} products")
                for i, product in enumerate(self.products):
                    if i % 10 == 0:
                        self.logger.info(f"Progress: {i}/{len(self.products)}")
                    await self._scrape_product_details(product)
                    await self._random_delay()
            
            self.logger.info(f"Scraping completed. Total products: {len(self.products)}")
            
        finally:
            if self.browser:
                await self.browser.close()
    
    def save_results(self, filename: Optional[str] = None):
        """Save scraped data to file."""
        if not self.products:
            self.logger.warning("No products to save")
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if self.config.output_format.lower() == "json":
            filename = filename or f"mediamarkt_products_{timestamp}.json"
            filepath = os.path.join(self.config.output_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(
                    [p.to_dict() for p in self.products],
                    f,
                    ensure_ascii=False,
                    indent=2
                )
            
        else:  # CSV
            filename = filename or f"mediamarkt_products_{timestamp}.csv"
            filepath = os.path.join(self.config.output_dir, filename)
            
            if self.products:
                fieldnames = list(self.products[0].to_dict().keys())
                
                with open(filepath, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for product in self.products:
                        writer.writerow(product.to_dict())
        
        self.logger.info(f"Results saved to {filepath}")
        return filepath


# ============================================================================
# CLI INTERFACE
# ============================================================================

async def main():
    """Main entry point."""
    import argparse
    
    scraper = argparse.Argumentscraper(
        description="MediaMarkt Scraper - Playwright Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic scraping
  python mediamarkt_playwright.py
  
  # With proxy and captcha solving
  python mediamarkt_playwright.py --proxy-host proxy.2prx.com --proxy-port 8080 \\
    --proxy-user myuser --proxy-pass mypass --captcha-key YOUR_2CAPTCHA_KEY
  
  # Specific categories only
  python mediamarkt_playwright.py --categories tv-audio,computing,gaming
  
  # Full product details
  python mediamarkt_playwright.py --details --format csv

More info: https://github.com/2scraper/mediamarkt-scraper
        """
    )
    
    # Output options
    scraper.add_argument('--format', choices=['json', 'csv'], default='json',
                        help='Output format (default: json)')
    scraper.add_argument('--output-dir', default='./output',
                        help='Output directory (default: ./output)')
    scraper.add_argument('--details', action='store_true',
                        help='Scrape full product details (slower)')
    
    # Category options
    scraper.add_argument('--categories', type=str, default='',
                        help='Comma-separated categories to scrape (default: all)')
    scraper.add_argument('--max-pages', type=int, default=0,
                        help='Max pages per category (0 = unlimited)')
    
    # Proxy options (2prx.com)
    scraper.add_argument('--proxy-host', default=os.getenv('PROXY_HOST', ''),
                        help='Proxy hostname (2prx.com)')
    scraper.add_argument('--proxy-port', type=int, default=int(os.getenv('PROXY_PORT', '0')),
                        help='Proxy port')
    scraper.add_argument('--proxy-user', default=os.getenv('PROXY_USER', ''),
                        help='Proxy username')
    scraper.add_argument('--proxy-pass', default=os.getenv('PROXY_PASS', ''),
                        help='Proxy password')
    
    # Captcha options (2captcha.com)
    scraper.add_argument('--captcha-key', default=os.getenv('TWOCAPTCHA_API_KEY', ''),
                        help='2captcha.com API key')
    
    # Browser options
    scraper.add_argument('--headless', action='store_true', default=True,
                        help='Run in headless mode (default: True)')
    scraper.add_argument('--no-headless', action='store_false', dest='headless',
                        help='Run with visible browser')
    scraper.add_argument('--no-stealth', action='store_true',
                        help='Disable stealth mode')
    scraper.add_argument('--no-fingerprint', action='store_true',
                        help='Disable fingerprint spoofing')
    
    # Timing options
    scraper.add_argument('--delay-min', type=float, default=1.0,
                        help='Minimum delay between requests (seconds)')
    scraper.add_argument('--delay-max', type=float, default=3.0,
                        help='Maximum delay between requests (seconds)')
    scraper.add_argument('--timeout', type=int, default=30000,
                        help='Page load timeout (milliseconds)')
    
    # Debug options
    scraper.add_argument('--debug', action='store_true',
                        help='Enable debug mode (saves HTML for troubleshooting)')
    scraper.add_argument('--debug-dir', default='./debug',
                        help='Directory to save debug HTML (default: ./debug)')
    
    args = scraper.parse_args()
    
    # Build configuration
    config = ScraperConfig(
        captcha_api_key=args.captcha_key,
        proxy_host=args.proxy_host,
        proxy_port=args.proxy_port,
        proxy_username=args.proxy_user,
        proxy_password=args.proxy_pass,
        output_format=args.format,
        output_dir=args.output_dir,
        headless=args.headless,
        timeout=args.timeout,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        use_stealth_mode=not args.no_stealth,
        use_fingerprint_spoofing=not args.no_fingerprint,
        categories=[c.strip() for c in args.categories.split(',') if c.strip()],
        max_pages_per_category=args.max_pages,
        debug=args.debug,
        debug_dir=args.debug_dir,
    )
    
    # Create and run scraper
    scraper = MediaMarktScraper(config)
    await scraper.scrape(scrape_details=args.details)
    scraper.save_results()
    
    print(f"\n✅ Scraped {len(scraper.products)} products")
    print(f"📁 Output saved to {config.output_dir}")


if __name__ == "__main__":
    asyncio.run(main())
