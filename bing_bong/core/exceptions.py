"""
커스텀 예외 클래스들
"""


class WebRTCError(Exception):
    """WebRTC 관련 오류"""
    pass


class MediaProcessingError(Exception):
    """미디어 처리 오류"""
    pass


class ConnectionError(Exception):
    """연결 관련 오류"""
    pass


class FaceDetectionError(Exception):
    """얼굴 인식 오류"""
    pass


class ConfigurationError(Exception):
    """설정 오류"""
    pass
