"""The free MT engines called directly: ModernMT and MyMemory.

Part of the main window (SupervertalerQt), moved here unchanged. The
methods run as the main window's own: `self` is the SupervertalerQt.
"""



class MTEnginesMixin:
    def call_modernmt(self, text: str, source_lang: str, target_lang: str, api_key: str = None) -> str:
        """Call ModernMT API"""
        try:
            import requests

            api_keys = self.load_api_keys()
            if not api_key:
                api_key = api_keys.get("modernmt")

            if not api_key:
                return f"[{self._api_key_problem('modernmt') or 'ModernMT requires API key'}]"

            # Map full language names to ISO codes
            lang_name_to_code = {
                'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
                'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
                'chinese': 'zh', 'japanese': 'ja', 'korean': 'ko', 'arabic': 'ar',
                'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
                'finnish': 'fi', 'greek': 'el', 'turkish': 'tr', 'czech': 'cs',
                'hungarian': 'hu', 'romanian': 'ro', 'bulgarian': 'bg', 'ukrainian': 'uk',
            }

            # Convert language - try name mapping first, then code extraction
            src_lower = source_lang.lower().strip()
            tgt_lower = target_lang.lower().strip()

            src_code = lang_name_to_code.get(src_lower, src_lower.split('-')[0].split('_')[0])
            tgt_code = lang_name_to_code.get(tgt_lower, tgt_lower.split('-')[0].split('_')[0])
            
            # ModernMT API endpoint
            url = "https://api.modernmt.com/translate"
            headers = {
                'MMT-ApiKey': api_key,
                'Content-Type': 'application/json'
            }
            data = {
                'q': text,
                'source': src_code,
                'target': tgt_code
            }
            
            response = requests.post(url, headers=headers, json=data, timeout=10, proxies=self._get_proxy_dict())
            response.raise_for_status()

            result = response.json()
            return result['data']['translation']

        except ImportError:
            return "[ModernMT requires: pip install requests]"
        except Exception as e:
            return f"[ModernMT error: {str(e)}]"

    def call_mymemory(self, text: str, source_lang: str, target_lang: str, api_key: str = None) -> str:
        """Call MyMemory Translation API (free tier available, simple REST API)"""
        try:
            import requests

            # MyMemory is free, but API key provides higher limits
            api_keys = self.load_api_keys()
            api_key = api_key or api_keys.get("mymemory")  # Optional, works without key

            # Map full language names to ISO codes
            lang_name_to_code = {
                'english': 'en', 'dutch': 'nl', 'german': 'de', 'french': 'fr',
                'spanish': 'es', 'italian': 'it', 'portuguese': 'pt', 'russian': 'ru',
                'chinese': 'zh', 'japanese': 'ja', 'korean': 'ko', 'arabic': 'ar',
                'polish': 'pl', 'swedish': 'sv', 'norwegian': 'no', 'danish': 'da',
                'finnish': 'fi', 'greek': 'el', 'turkish': 'tr', 'czech': 'cs',
                'hungarian': 'hu', 'romanian': 'ro', 'bulgarian': 'bg', 'ukrainian': 'uk',
                'hebrew': 'he', 'thai': 'th', 'vietnamese': 'vi', 'indonesian': 'id',
                'malay': 'ms', 'hindi': 'hi', 'bengali': 'bn', 'tamil': 'ta',
            }

            # Convert language - try name mapping first, then code extraction
            src_lower = source_lang.lower().strip()
            tgt_lower = target_lang.lower().strip()

            src_code = lang_name_to_code.get(src_lower, src_lower.split('-')[0].split('_')[0])
            tgt_code = lang_name_to_code.get(tgt_lower, tgt_lower.split('-')[0].split('_')[0])
            
            # MyMemory API endpoint
            url = "https://api.mymemory.translated.net/get"
            params = {
                'q': text,
                'langpair': f"{src_code}|{tgt_code}"
            }
            if api_key:
                params['key'] = api_key
            
            response = requests.get(url, params=params, timeout=10, proxies=self._get_proxy_dict())
            response.raise_for_status()

            result = response.json()
            if result.get('responseStatus') == 200:
                return result['responseData']['translatedText']
            else:
                return f"[MyMemory error: {result.get('responseDetails', 'Unknown error')}]"
            
        except ImportError:
            return "[MyMemory requires: pip install requests]"
        except Exception as e:
            return f"[MyMemory error: {str(e)}]"
