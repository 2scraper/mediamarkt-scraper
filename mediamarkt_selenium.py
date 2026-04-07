#!/usr/bin/env python3
"""
MediaMarkt Scraper - Selenium Edition
======================================
High-performance web scraper for MediaMarkt product data.
Supports all categories, anti-detection, captcha solving, and proxy rotation.

GitHub: https://github.com/2parser/mediamarkt-parser
Captcha Solving: https://2captcha.com
Proxy Service: https://2prx.com

License: MIT
"""

import json
import csv
import random
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict, field
from urllib.parse import urljoin

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException, NoSuchElementException
except ImportError:
    raise ImportError("Please install selenium: pip install selenium")

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    ChromeDriverManager = None
    print("Warning: webdriver-manager not installed. Run: pip install webdriver-manager")

try:
    from selenium_stealth import stealth
except ImportError:
    stealth = None
    print("Warning: selenium-stealth not installed. Run: pip install selenium-stealth")

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
    timeout: int = 30
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
    
    # Chrome driver path (optional, will auto-download if not set)
    chromedriver_path: str = ""
    
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
    ]
    
    SCREEN_RESOLUTIONS = [
        (1920, 1080),
        (1366, 768),
        (1536, 864),
        (1440, 900),
        (1280, 720),
        (2560, 1440),
    ]
    
    LANGUAGES = ["de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"]
    TIMEZONES = ["Europe/Berlin", "Europe/Vienna", "Europe/Zurich"]
    
    @classmethod
    def generate(cls) -> Dict[str, Any]:
        """Generate a random fingerprint."""
        resolution = random.choice(cls.SCREEN_RESOLUTIONS)
        return {
            "user_agent": random.choice(cls.USER_AGENTS),
            "screen_width": resolution[0],
            "screen_height": resolution[1],
            "language": random.choice(cls.LANGUAGES),
            "timezone": random.choice(cls.TIMEZONES),
            "platform": "Win32",
        }


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
    
    def solve_recaptcha_v2(self, sitekey: str, page_url: str) -> Optional[str]:
        """Solve reCAPTCHA v2."""
        if not self.solver:
            logging.warning("2captcha not configured. Cannot solve captcha.")
            return None
        
        try:
            logging.info(f"Solving reCAPTCHA v2 for {page_url}")
            result = self.solver.recaptcha(sitekey=sitekey, url=page_url)
            logging.info("reCAPTCHA v2 solved successfully")
            return result['code']
        except Exception as e:
            logging.error(f"Failed to solve reCAPTCHA v2: {e}")
            return None
    
    def solve_recaptcha_v3(self, sitekey: str, page_url: str, action: str = "verify") -> Optional[str]:
        """Solve reCAPTCHA v3."""
        if not self.solver:
            logging.warning("2captcha not configured. Cannot solve captcha.")
            return None
        
        try:
            logging.info(f"Solving reCAPTCHA v3 for {page_url}")
            result = self.solver.recaptcha(
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
    
    def solve_hcaptcha(self, sitekey: str, page_url: str) -> Optional[str]:
        """Solve hCaptcha."""
        if not self.solver:
            logging.warning("2captcha not configured. Cannot solve captcha.")
            return None
        
        try:
            logging.info(f"Solving hCaptcha for {page_url}")
            result = self.solver.hcaptcha(sitekey=sitekey, url=page_url)
            logging.info("hCaptcha solved successfully")
            return result['code']
        except Exception as e:
            logging.error(f"Failed to solve hCaptcha: {e}")
            return None
    
    def solve_turnstile(self, sitekey: str, page_url: str) -> Optional[str]:
        """Solve Cloudflare Turnstile."""
        if not self.solver:
            logging.warning("2captcha not configured. Cannot solve captcha.")
            return None
        
        try:
            logging.info(f"Solving Turnstile for {page_url}")
            result = self.solver.turnstile(sitekey=sitekey, url=page_url)
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
    MediaMarkt web scraper using Selenium.
    
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
    
    def __init__(self, config: ScraperConfig):
        self.config = config
        self.driver: Optional[webdriver.Chrome] = None
        self.products: List[Product] = []
        self.captcha_solver = CaptchaSolver(config.captcha_api_key)
        self.fingerprint = FingerprintGenerator.generate()
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        
        # Create output directory
        os.makedirs(config.output_dir, exist_ok=True)
    
    def _setup_driver(self):
        """Initialize Chrome WebDriver with anti-detection settings."""
        options = Options()
        
        # Basic options
        if self.config.headless:
            options.add_argument("--headless=new")
        
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-infobars")
        options.add_argument(f"--window-size={self.fingerprint['screen_width']},{self.fingerprint['screen_height']}")
        
        # Anti-detection
        if self.config.use_fingerprint_spoofing:
            options.add_argument(f"--user-agent={self.fingerprint['user_agent']}")
            options.add_argument(f"--lang={self.fingerprint['language']}")
        
        # Disable automation flags
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        
        # Proxy configuration for 2prx.com
        if self.config.proxy_host:
            if self.config.proxy_username:
                # For authenticated proxy, we need to use an extension
                self._setup_proxy_extension(options)
            else:
                proxy_str = f"{self.config.proxy_host}:{self.config.proxy_port}"
                options.add_argument(f"--proxy-server=http://{proxy_str}")
            self.logger.info(f"Using proxy: {self.config.proxy_host}:{self.config.proxy_port}")
        
        # Initialize driver
        if self.config.chromedriver_path:
            service = Service(self.config.chromedriver_path)
        elif ChromeDriverManager:
            service = Service(ChromeDriverManager().install())
        else:
            service = Service()
        
        self.driver = webdriver.Chrome(service=service, options=options)
        self.driver.implicitly_wait(10)
        self.driver.set_page_load_timeout(self.config.timeout)
        
        # Apply stealth mode
        if self.config.use_stealth_mode and stealth:
            stealth(
                self.driver,
                languages=["de-DE", "de", "en-US", "en"],
                vendor="Google Inc.",
                platform="Win32",
                webgl_vendor="Intel Inc.",
                renderer="Intel Iris OpenGL Engine",
                fix_hairline=True,
            )
        
        # Execute anti-detection scripts
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5]
                });
                
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['de-DE', 'de', 'en-US', 'en']
                });
                
                window.chrome = {
                    runtime: {}
                };
            """
        })
        
        self.logger.info("Chrome WebDriver initialized with anti-detection settings")
    
    def _setup_proxy_extension(self, options: Options):
        """Create a Chrome extension for authenticated proxy."""
        import zipfile
        import tempfile
        
        manifest_json = """
        {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Chrome Proxy",
            "permissions": [
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking"
            ],
            "background": {
                "scripts": ["background.js"]
            },
            "minimum_chrome_version":"22.0.0"
        }
        """
        
        background_js = f"""
        var config = {{
                mode: "fixed_servers",
                rules: {{
                    singleProxy: {{
                        scheme: "http",
                        host: "{self.config.proxy_host}",
                        port: parseInt({self.config.proxy_port})
                    }},
                    bypassList: ["localhost"]
                }}
            }};
        
        chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});
        
        function callbackFn(details) {{
            return {{
                authCredentials: {{
                    username: "{self.config.proxy_username}",
                    password: "{self.config.proxy_password}"
                }}
            }};
        }}
        
        chrome.webRequest.onAuthRequired.addListener(
            callbackFn,
            {{urls: ["<all_urls>"]}},
            ['blocking']
        );
        """
        
        # Create temporary extension
        with tempfile.TemporaryDirectory() as tmpdir:
            extension_path = os.path.join(tmpdir, "proxy_extension.zip")
            with zipfile.ZipFile(extension_path, 'w') as zp:
                zp.writestr("manifest.json", manifest_json)
                zp.writestr("background.js", background_js)
            options.add_extension(extension_path)
    
    def _random_delay(self):
        """Add random delay to mimic human behavior."""
        delay = random.uniform(self.config.delay_min, self.config.delay_max)
        time.sleep(delay)
    
    def _detect_and_solve_captcha(self) -> bool:
        """Detect and solve captcha if present (reCAPTCHA, hCaptcha, or Cloudflare Turnstile)."""
        try:
            # Check page title for Cloudflare challenge
            title = self.driver.title
            content = self.driver.page_source
            
            # Check for Cloudflare Turnstile
            if "Nur einen Moment" in title or "Just a moment" in title or "challenges.cloudflare.com/turnstile" in content:
                self.logger.info("Cloudflare Turnstile detected!")
                
                # Try to find turnstile sitekey
                sitekey = None
                
                # Method 1: From turnstile widget
                try:
                    turnstile_widget = self.driver.find_element(By.CSS_SELECTOR, '[class*="cf-turnstile"]')
                    sitekey = turnstile_widget.get_attribute('data-sitekey')
                except NoSuchElementException:
                    pass
                
                # Method 2: Extract from script in page
                if not sitekey:
                    sitekey_match = re.search(r'sitekey["\']?\s*[=:]\s*["\']([^"\']+)["\']', content)
                    if sitekey_match:
                        sitekey = sitekey_match.group(1)
                
                # Method 3: Common MediaMarkt Turnstile sitekey
                if not sitekey:
                    sitekey = "0x4AAAAAAADQ_tLiTFhG9-7K"
                
                if sitekey and self.captcha_solver.solver:
                    self.logger.info(f"Attempting to solve Turnstile with sitekey: {sitekey[:20]}...")
                    token = self.captcha_solver.solve_turnstile(sitekey, self.driver.current_url)
                    
                    if token:
                        # Inject the token
                        self.driver.execute_script(f'''
                            const responses = document.querySelectorAll('[name="cf-turnstile-response"], [id*="cf-chl-widget"]');
                            responses.forEach(el => el.value = "{token}");
                            
                            // Try to submit
                            const form = document.querySelector('form');
                            if (form) form.submit();
                        ''')
                        
                        # Wait for redirect
                        time.sleep(3)
                        
                        # Check if we passed
                        new_title = self.driver.title
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
            try:
                recaptcha = self.driver.find_element(By.CSS_SELECTOR, '[data-sitekey]')
                sitekey = recaptcha.get_attribute('data-sitekey')
                if sitekey:
                    token = self.captcha_solver.solve_recaptcha_v2(sitekey, self.driver.current_url)
                    if token:
                        self.driver.execute_script(f'''
                            document.getElementById("g-recaptcha-response").innerHTML = "{token}";
                        ''')
                        submit_btn = self.driver.find_element(By.CSS_SELECTOR, '[type="submit"]')
                        submit_btn.click()
                        self._random_delay()
                        return True
            except NoSuchElementException:
                pass
            
            # Check for hCaptcha
            try:
                hcaptcha = self.driver.find_element(By.CSS_SELECTOR, '[data-hcaptcha-sitekey]')
                sitekey = hcaptcha.get_attribute('data-hcaptcha-sitekey')
                if sitekey:
                    token = self.captcha_solver.solve_hcaptcha(sitekey, self.driver.current_url)
                    if token:
                        self.driver.execute_script(f'''
                            document.querySelector("[name='h-captcha-response']").value = "{token}";
                        ''')
                        self._random_delay()
                        return True
            except NoSuchElementException:
                pass
            
            return False
        except Exception as e:
            self.logger.error(f"Error handling captcha: {e}")
            return False
    
    def _accept_cookies(self):
        """Accept cookie consent banner."""
        cookie_selectors = [
            '[data-test="pwa-consent-layer-accept-all"]',
            '#onetrust-accept-btn-handler',
            '[id*="accept"]',
            'button[class*="accept"]',
        ]
        
        for selector in cookie_selectors:
            try:
                button = WebDriverWait(self.driver, 3).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                )
                button.click()
                self.logger.info("Cookie consent accepted")
                self._random_delay()
                return
            except:
                continue
    
    def _extract_product_data(self, product_element) -> Optional[Product]:
        """Extract product data from a product card element."""
        try:
            product = Product()
            product.scraped_at = datetime.now().isoformat()
            
            # Product URL and ID
            try:
                link = product_element.find_element(By.CSS_SELECTOR, 'a[href*="/product/"]')
                href = link.get_attribute('href')
                product.url = href if href.startswith('http') else urljoin(self.BASE_URL, href)
                match = re.search(r'/product/_([^/]+)-(\d+)\.html', href)
                if match:
                    product.id = match.group(2)
            except NoSuchElementException:
                pass
            
            # Product name
            try:
                name_el = product_element.find_element(By.CSS_SELECTOR, '[data-test="product-title"], h2, .product-title')
                product.name = name_el.text.strip()
            except NoSuchElementException:
                pass
            
            # Brand
            try:
                brand_el = product_element.find_element(By.CSS_SELECTOR, '[data-test="product-brand"], .brand')
                product.brand = brand_el.text.strip()
            except NoSuchElementException:
                if product.name:
                    product.brand = product.name.split()[0]
            
            # Price
            try:
                price_el = product_element.find_element(By.CSS_SELECTOR, '[data-test="product-price"], .price, [class*="price"]')
                price_text = price_el.text
                price_match = re.search(r'([\d.]+),(\d{2})', price_text)
                if price_match:
                    price_str = price_match.group(1).replace('.', '') + '.' + price_match.group(2)
                    product.price = float(price_str)
            except NoSuchElementException:
                pass
            
            # Original price
            try:
                orig_price_el = product_element.find_element(By.CSS_SELECTOR, '[data-test="product-price-strike"], .price-old')
                orig_price_text = orig_price_el.text
                orig_match = re.search(r'([\d.]+),(\d{2})', orig_price_text)
                if orig_match:
                    orig_str = orig_match.group(1).replace('.', '') + '.' + orig_match.group(2)
                    product.original_price = float(orig_str)
            except NoSuchElementException:
                pass
            
            # Rating
            try:
                rating_el = product_element.find_element(By.CSS_SELECTOR, '[data-test="product-rating"], .rating')
                rating_text = rating_el.get_attribute('aria-label') or rating_el.text
                rating_match = re.search(r'([\d,\.]+)', rating_text)
                if rating_match:
                    product.rating = float(rating_match.group(1).replace(',', '.'))
            except NoSuchElementException:
                pass
            
            # Reviews count
            try:
                reviews_el = product_element.find_element(By.CSS_SELECTOR, '[data-test="product-reviews"], .reviews-count')
                reviews_text = reviews_el.text
                reviews_match = re.search(r'(\d+)', reviews_text)
                if reviews_match:
                    product.reviews_count = int(reviews_match.group(1))
            except NoSuchElementException:
                pass
            
            # Availability
            try:
                avail_el = product_element.find_element(By.CSS_SELECTOR, '[data-test="product-availability"], .availability')
                product.availability = avail_el.text.strip()
            except NoSuchElementException:
                pass
            
            # Image URL
            try:
                img_el = product_element.find_element(By.TAG_NAME, 'img')
                product.image_url = img_el.get_attribute('src') or img_el.get_attribute('data-src') or ""
            except NoSuchElementException:
                pass
            
            return product if product.name else None
            
        except Exception as e:
            self.logger.error(f"Error extracting product data: {e}")
            return None
    
    def _scrape_product_details(self, product: Product) -> Product:
        """Scrape additional details from product page."""
        try:
            self.driver.get(product.url)
            self._random_delay()
            
            # Check for captcha
            self._detect_and_solve_captcha()
            
            # Description
            try:
                desc_el = self.driver.find_element(By.CSS_SELECTOR, '[data-test="product-description"], .product-description')
                product.description = desc_el.text.strip()
            except NoSuchElementException:
                pass
            
            # Specifications
            try:
                spec_rows = self.driver.find_elements(By.CSS_SELECTOR, '[data-test="specification-row"], .spec-row, tr')
                for row in spec_rows[:20]:
                    try:
                        label_el = row.find_element(By.CSS_SELECTOR, 'th, .spec-label, dt')
                        value_el = row.find_element(By.CSS_SELECTOR, 'td, .spec-value, dd')
                        label = label_el.text.strip()
                        value = value_el.text.strip()
                        if label and value:
                            product.specifications[label] = value
                    except:
                        continue
            except NoSuchElementException:
                pass
            
            # EAN
            try:
                ean_el = self.driver.find_element(By.CSS_SELECTOR, '[itemprop="gtin13"], [data-test="product-ean"]')
                product.ean = (ean_el.get_attribute('content') or ean_el.text).strip()
            except NoSuchElementException:
                pass
            
            # SKU
            try:
                sku_el = self.driver.find_element(By.CSS_SELECTOR, '[itemprop="sku"], [data-test="product-sku"]')
                product.sku = (sku_el.get_attribute('content') or sku_el.text).strip()
            except NoSuchElementException:
                pass
            
        except Exception as e:
            self.logger.error(f"Error scraping product details for {product.url}: {e}")
        
        return product
    
    def _scrape_category(self, category_name: str, category_url: str):
        """Scrape all products from a category."""
        self.logger.info(f"Scraping category: {category_name}")
        
        full_url = urljoin(self.BASE_URL, category_url)
        page_num = 1
        category_products = 0
        captcha_retries = 0
        max_captcha_retries = 3
        
        while True:
            if self.config.max_pages_per_category > 0 and page_num > self.config.max_pages_per_category:
                break
            
            page_url = f"{full_url}?page={page_num}" if page_num > 1 else full_url
            self.logger.info(f"Scraping page {page_num}: {page_url}")
            
            try:
                self.driver.get(page_url)
                self._random_delay()
                
                # Check for Cloudflare or CAPTCHA and try to solve it
                captcha_solved = self._detect_and_solve_captcha()
                
                if captcha_solved:
                    self.logger.info("CAPTCHA solved, waiting for page reload...")
                    time.sleep(3)
                    self.driver.get(page_url)
                    self._random_delay()
                
                # Check if still on CAPTCHA page
                title = self.driver.title
                if "Nur einen Moment" in title or "Just a moment" in title:
                    captcha_retries += 1
                    if captcha_retries >= max_captcha_retries:
                        self.logger.error(f"Failed to bypass Cloudflare after {max_captcha_retries} attempts")
                        self.logger.info("Try using: --captcha-key YOUR_2CAPTCHA_KEY or residential proxy from 2prx.com")
                        break
                    self.logger.warning(f"Still on CAPTCHA page, retry {captcha_retries}/{max_captcha_retries}")
                    time.sleep(5)
                    continue
                
                # Reset captcha retry counter on success
                captcha_retries = 0
                
                if page_num == 1:
                    self._accept_cookies()
                
                # Wait for products with multiple selectors
                product_cards = []
                for selector in self.PRODUCT_SELECTORS:
                    try:
                        WebDriverWait(self.driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                        )
                        product_cards = self.driver.find_elements(By.CSS_SELECTOR, selector)
                        if product_cards:
                            self.logger.debug(f"Found products with selector: {selector}")
                            break
                    except TimeoutException:
                        continue
                
                # Fallback: try to find product links
                if not product_cards:
                    try:
                        product_cards = self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="/de/product/"]')
                    except:
                        pass
                
                if not product_cards:
                    self.logger.warning(f"No products found on page {page_num}")
                    break
                
                self.logger.info(f"Found {len(product_cards)} products on page {page_num}")
                
                for card in product_cards:
                    product = self._extract_product_data(card)
                    if product and product.name:
                        product.category = category_name
                        self.products.append(product)
                        category_products += 1
                
                # Check for next page
                next_found = False
                for next_sel in ['[data-test="pagination-next"]:not([disabled])', '[data-testid="pagination-next"]', 'a[rel="next"]']:
                    try:
                        next_button = self.driver.find_element(By.CSS_SELECTOR, next_sel)
                        if next_button.is_enabled():
                            next_found = True
                            break
                    except NoSuchElementException:
                        continue
                
                if not next_found:
                    break
                
                page_num += 1
                
            except Exception as e:
                self.logger.error(f"Error on page {page_num}: {e}")
                if page_num == 1:
                    break
                continue
        
        self.logger.info(f"Completed category {category_name}: {category_products} products scraped")
    
    def scrape(self, scrape_details: bool = False):
        """Main scraping method."""
        self.logger.info("Starting MediaMarkt scraper (Selenium)")
        
        try:
            self._setup_driver()
            
            categories = {}
            if self.config.categories:
                categories = {k: v for k, v in self.CATEGORIES.items() if k in self.config.categories}
            else:
                categories = self.CATEGORIES
            
            self.logger.info(f"Will scrape {len(categories)} categories")
            
            for cat_name, cat_url in categories.items():
                self._scrape_category(cat_name, cat_url)
                self._random_delay()
            
            if scrape_details and self.products:
                self.logger.info(f"Scraping details for {len(self.products)} products")
                for i, product in enumerate(self.products):
                    if i % 10 == 0:
                        self.logger.info(f"Progress: {i}/{len(self.products)}")
                    self._scrape_product_details(product)
                    self._random_delay()
            
            self.logger.info(f"Scraping completed. Total products: {len(self.products)}")
            
        finally:
            if self.driver:
                self.driver.quit()
    
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
        else:
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

def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="MediaMarkt Scraper - Selenium Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic scraping
  python mediamarkt_selenium.py
  
  # With proxy and captcha solving
  python mediamarkt_selenium.py --proxy-host proxy.2prx.com --proxy-port 8080 \\
    --proxy-user myuser --proxy-pass mypass --captcha-key YOUR_2CAPTCHA_KEY
  
  # Specific categories only
  python mediamarkt_selenium.py --categories tv-audio,computing,gaming

More info: https://github.com/2parser/mediamarkt-parser
        """
    )
    
    # Output options
    parser.add_argument('--format', choices=['json', 'csv'], default='json')
    parser.add_argument('--output-dir', default='./output')
    parser.add_argument('--details', action='store_true')
    
    # Category options
    parser.add_argument('--categories', type=str, default='')
    parser.add_argument('--max-pages', type=int, default=0)
    
    # Proxy options (2prx.com)
    parser.add_argument('--proxy-host', default=os.getenv('PROXY_HOST', ''))
    parser.add_argument('--proxy-port', type=int, default=int(os.getenv('PROXY_PORT', '0')))
    parser.add_argument('--proxy-user', default=os.getenv('PROXY_USER', ''))
    parser.add_argument('--proxy-pass', default=os.getenv('PROXY_PASS', ''))
    
    # Captcha options (2captcha.com)
    parser.add_argument('--captcha-key', default=os.getenv('TWOCAPTCHA_API_KEY', ''))
    
    # Browser options
    parser.add_argument('--headless', action='store_true', default=True)
    parser.add_argument('--no-headless', action='store_false', dest='headless')
    parser.add_argument('--no-stealth', action='store_true')
    parser.add_argument('--no-fingerprint', action='store_true')
    parser.add_argument('--chromedriver', default='')
    
    # Timing options
    parser.add_argument('--delay-min', type=float, default=1.0)
    parser.add_argument('--delay-max', type=float, default=3.0)
    parser.add_argument('--timeout', type=int, default=30)
    
    args = parser.parse_args()
    
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
        chromedriver_path=args.chromedriver,
    )
    
    scraper = MediaMarktScraper(config)
    scraper.scrape(scrape_details=args.details)
    scraper.save_results()
    
    print(f"\n✅ Scraped {len(scraper.products)} products")
    print(f"📁 Output saved to {config.output_dir}")


if __name__ == "__main__":
    main()
