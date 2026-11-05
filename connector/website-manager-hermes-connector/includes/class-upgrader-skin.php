<?php
/**
 * Quiet upgrader skin that captures feedback and errors per bulk item.
 *
 * Loaded lazily, only after wp-admin/includes/class-wp-upgrader.php.
 *
 * @package WebsiteManagerHermesConnector
 */

namespace WebsiteManagerHermesConnector;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Collects upgrader messages instead of printing them.
 */
class Upgrader_Skin extends \Automatic_Upgrader_Skin {

	/**
	 * Error messages keyed by bulk index (0-based). Key -1 holds errors raised
	 * outside of an item (for example a filesystem connection failure).
	 *
	 * @var array<int, string[]>
	 */
	protected $item_errors = array();

	/**
	 * Feedback messages keyed by bulk index (0-based), -1 for global.
	 *
	 * @var array<int, string[]>
	 */
	protected $item_messages = array();

	/**
	 * Returns the 0-based index of the bulk item being processed, or -1.
	 *
	 * @return int
	 */
	protected function current_index() {
		if ( isset( $this->upgrader ) && is_object( $this->upgrader ) && ! empty( $this->upgrader->update_current ) ) {
			return (int) $this->upgrader->update_current - 1;
		}
		return -1;
	}

	/**
	 * Records feedback (strings or WP_Error) for the current item.
	 *
	 * @param string|\WP_Error $feedback Message or error.
	 * @param mixed            ...$args  Optional sprintf args.
	 * @return void
	 */
	public function feedback( $feedback, ...$args ) {
		$before = count( $this->messages );
		parent::feedback( $feedback, ...$args );
		if ( count( $this->messages ) > $before ) {
			$message = self::plain( (string) end( $this->messages ) );
			// Maintenance mode toggles wrap the whole run, not one item.
			$index = ( is_string( $feedback ) && in_array( $feedback, array( 'maintenance_start', 'maintenance_end' ), true ) ) ? -1 : $this->current_index();
			$this->item_messages[ $index ][] = $message;
		}
	}

	/**
	 * Records errors for the current item.
	 *
	 * @param string|\WP_Error $errors Errors.
	 * @return void
	 */
	public function error( $errors ) {
		$messages = array();
		if ( is_string( $errors ) ) {
			$messages[] = isset( $this->upgrader->strings[ $errors ] ) ? $this->upgrader->strings[ $errors ] : $errors;
		} elseif ( is_wp_error( $errors ) && $errors->has_errors() ) {
			foreach ( $errors->get_error_messages() as $message ) {
				$data = $errors->get_error_data();
				if ( is_string( $data ) && '' !== $data ) {
					$message .= ' ' . $data;
				}
				$messages[] = $message;
			}
		}
		foreach ( $messages as $message ) {
			$this->item_errors[ $this->current_index() ][] = self::plain( (string) $message );
		}
		parent::error( $errors );
	}

	/**
	 * Converts an upgrader string to plain text (no tags, decoded entities).
	 *
	 * @param string $message Message.
	 * @return string
	 */
	public static function plain( $message ) {
		return trim( html_entity_decode( wp_strip_all_tags( $message ), ENT_QUOTES, 'UTF-8' ) );
	}

	/**
	 * Errors recorded for a bulk item.
	 *
	 * @param int $index 0-based index, -1 for global errors.
	 * @return string[]
	 */
	public function get_item_errors( $index ) {
		return isset( $this->item_errors[ $index ] ) ? array_values( array_unique( $this->item_errors[ $index ] ) ) : array();
	}

	/**
	 * Feedback recorded for a bulk item.
	 *
	 * @param int $index 0-based index, -1 for global messages.
	 * @return string[]
	 */
	public function get_item_messages( $index ) {
		return isset( $this->item_messages[ $index ] ) ? array_values( $this->item_messages[ $index ] ) : array();
	}
}
